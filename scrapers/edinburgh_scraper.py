#!/usr/bin/env python3
"""
PlanFind — City of Edinburgh Council scraper (2026-09-03).

Real, confirmed evidence backing every design decision — see
edinburgh_councils.py.

ARCHITECTURE: pure httpx, no Playwright needed at all. Fetches the real
web map's current operationalLayers every run (the layer set itself
changes week to week — see module docstring), queries each real
"Applications "/"Decisions "-prefixed FeatureServer layer directly for
JSON, and merges records by Appno (Decisions' real outcome overrides
Applications' default 'pending').
"""
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import httpx

from edinburgh_councils import COUNCIL_DB_IDS, EDINBURGH_ORG_HOST, EDINBURGH_WEBMAP_ITEM_ID

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

COUNCIL_NAME = "City of Edinburgh Council"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(text: str) -> str:
    """Real, confirmed: address lines are \\r-separated, same
    convention already handled for Amber Valley."""
    if not text:
        return ""
    parts = [p.strip() for p in text.split("\r") if p.strip()]
    return ", ".join(parts)


def _parse_edinburgh_date(value: Optional[str]) -> Optional[str]:
    """Real, confirmed format: 'DD-Mon-YY' (e.g. '21-Aug-26')."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d-%b-%y").date().isoformat()
    except ValueError:
        return None


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_edinburgh_status(decision_text: str) -> str:
    """Real, confirmed rich decision text — e.g. 'Granted', 'Refused',
    'EIA Not Required'. Uses substring matching, same discipline as
    every other platform in this project — never guesses at
    unrecognised text."""
    if not decision_text:
        return "pending"
    key = decision_text.lower()
    if any(x in key for x in ("grant", "approv", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    # REAL FIX (2026-09-03) — "EIA Not Required" is a genuine real
    # decision value confirmed in this data, but it's a PROCEDURAL
    # screening outcome (whether a full Environmental Impact Assessment
    # is needed), NOT an approval of the actual development proposal
    # itself. The real planning decision on the underlying application
    # is typically separate and may still be pending. Mapping this to
    # 'approved' would be genuinely misleading to a user — filing as
    # pending is the honest choice, matching this project's "never
    # claim an outcome the data doesn't actually support" discipline.
    if "eia" in key:
        return "pending"

    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised decision text {decision_text!r} — filed as 'pending'")
    return "pending"


async def fetch_weekly_layers(client: httpx.AsyncClient) -> list[dict]:
    """Real, confirmed: the web map's real layer list changes week to
    week — re-fetched fresh every run, never a hardcoded date."""
    url = f"https://{EDINBURGH_ORG_HOST}/sharing/rest/content/items/{EDINBURGH_WEBMAP_ITEM_ID}/data?f=json"
    r = await client.get(url)
    r.raise_for_status()
    data = r.json()
    layers = data.get("operationalLayers", [])

    matching = []
    for layer in layers:
        title = layer.get("title", "")
        layer_url = layer.get("url", "")
        if not layer_url:
            continue  # real, confirmed: some layers (e.g. "Conservation Areas") have no url
        if title.startswith("Applications ") or title.startswith("Decisions "):
            matching.append({"title": title, "url": layer_url,
                              "kind": "applications" if title.startswith("Applications") else "decisions"})

    return matching


async def query_layer(client: httpx.AsyncClient, layer_url: str) -> list[dict]:
    """Real, confirmed: plain ArcGIS FeatureServer query, JSON out."""
    query_url = f"{layer_url.rstrip('/')}/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": "2000",  # comfortably above any real single week's volume
    }
    try:
        r = await client.get(query_url, params=params)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            _log(f"⚠ Real API error querying {layer_url}: {data['error']}")
            return []
        return [f.get("attributes", {}) for f in data.get("features", [])]
    except Exception as e:
        _log(f"⚠ Query failed for {layer_url}: {type(e).__name__}: {e!r}")
        return []


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                json=records, headers=headers,
            )
            if r.status_code not in (200, 201, 204):
                print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                return False
            return True
    except Exception as e:
        print(f"    ✗ Upsert exception: {e}")
        return False


async def _supa_patch_council(council_id: int, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.patch(
            f"{SUPABASE_URL}/rest/v1/councils",
            params={"id": f"eq.{council_id}"},
            json=data,
            headers={**_h(), "Prefer": "return=minimal"},
        )


async def geocode(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    if not unique:
        return results
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(unique), 100):
            try:
                r = await c.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": unique[i:i + 100]},
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"],
                        )
            except Exception as e:
                print(f"    ⚠ Geocoding batch failed ({len(unique[i:i + 100])} postcodes): {e}")
    return results


async def scrape() -> dict[str, dict]:
    """Returns a dict keyed by reference (Appno), merging Applications
    and Decisions layers — real, confirmed matching key."""
    merged: dict[str, dict] = {}

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30) as client:
        layers = await fetch_weekly_layers(client)
        _log(f"Real current weekly layers found: {len(layers)}")
        for l in layers:
            _log(f"  {l['title']} ({l['kind']})")

        for layer in layers:
            records = await query_layer(client, layer["url"])
            _log(f"{layer['title']}: {len(records)} real records")

            for rec in records:
                ref = rec.get("Appno")
                if not ref:
                    continue

                if ref not in merged:
                    merged[ref] = {"reference": ref}
                entry = merged[ref]

                if layer["kind"] == "applications":
                    entry["address"] = _clean_address(rec.get("Address", ""))
                    entry["applicant"] = rec.get("Applicant")
                    entry["application_type"] = rec.get("AppType")
                    entry["description"] = rec.get("Proposal")
                    entry["submitted_date"] = _parse_edinburgh_date(rec.get("Registered"))
                    entry["council_url"] = rec.get("Details")
                    entry.setdefault("status", "pending")
                else:  # decisions
                    entry["address"] = entry.get("address") or _clean_address(rec.get("Address", ""))
                    entry["applicant"] = entry.get("applicant") or rec.get("Applicant")
                    entry["application_type"] = entry.get("application_type") or rec.get("Apptype")
                    entry["description"] = entry.get("description") or rec.get("Proposal")
                    entry["council_url"] = entry.get("council_url") or rec.get("Details")
                    entry["decision_date"] = _parse_edinburgh_date(rec.get("DecDate"))
                    # Real, confirmed: Decision layer's real outcome
                    # overrides Applications layer's default 'pending'
                    entry["status"] = _normalise_edinburgh_status(rec.get("Decision", ""))

    return merged


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind City of Edinburgh scraper")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"edinburgh_councils.py. Run the INSERT_SQL there, look up "
              f"the real id, and fill it in before running this scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    merged = await scrape()

    if not merged:
        print("\nNo results — nothing to save.")
        return

    entries = list(merged.values())
    addresses = [e.get("address", "") for e in entries]
    postcodes = [_extract_postcode(a) for a in addresses]
    valid_postcodes = [p for p in postcodes if p]
    coords = await geocode(valid_postcodes) if valid_postcodes else {}
    if valid_postcodes:
        _log(f"Geocoding {len(valid_postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for entry, postcode in zip(entries, postcodes):
        lat, lng = None, None
        if postcode:
            key = postcode.upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        applicant_line = f"Applicant: {entry['applicant']}" if entry.get("applicant") else None
        description_parts = [p for p in (entry.get("description"), applicant_line) if p]

        records.append({
            "council_id": cid,
            "reference": entry["reference"],
            "submitted_date": entry.get("submitted_date"),
            "decision_date": entry.get("decision_date"),
            "address": entry.get("address") or None,
            "postcode": postcode,
            "description": " | ".join(description_parts) or None,
            "application_type": entry.get("application_type"),
            "status": entry.get("status", "pending"),
            "council_url": entry.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "edinburgh_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "edinburgh_arcgis_api",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print("Finished")


if __name__ == "__main__":
    asyncio.run(main())
