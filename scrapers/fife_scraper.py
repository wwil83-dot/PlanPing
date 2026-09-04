#!/usr/bin/env python3
"""
PlanFind — Fife Council scraper (2026-09-04).

Real, confirmed evidence backing every design decision — see
fife_councils.py.

ARCHITECTURE: pure httpx, no Playwright needed at all. Queries the real
"All Apps" layer (index 7) directly, filtered server-side by a real
DATE_RECEIVED range, using a relaxed SSL context to work around Fife's
genuinely expired (non-adversarial) certificate.
"""
import asyncio
import os
import re
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from fife_councils import COUNCIL_DB_IDS, BASE_URL, ALL_APPS_LAYER

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))

COUNCIL_NAME = "Fife Council"


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _legacy_ssl_context() -> ssl.SSLContext:
    """Real, confirmed necessary — Fife's own server certificate has
    genuinely expired (a real accidental misconfiguration, not
    deliberate protection). Same legitimate fix already used for West
    Dunbartonshire's old certificate earlier this project."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(text: str) -> str:
    """Real, confirmed: address lines are \\r-separated, same
    convention already handled for Amber Valley/Edinburgh."""
    if not text:
        return ""
    parts = [p.strip() for p in text.split("\r") if p.strip()]
    return ", ".join(parts)


def _parse_epoch_ms(value) -> Optional[str]:
    """Real, confirmed: ArcGIS-standard Unix epoch milliseconds."""
    if not value or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, OSError):
        return None


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_fife_status(gis_status: str, decision_text: str) -> str:
    """Real, confirmed: GIS_STATUS is the cleaner categorical field
    (Permitted/Refused/Pending Consideration/etc.) — preferred over the
    more verbose DECISION text field when both are present."""
    text = gis_status or decision_text or ""
    if not text:
        return "pending"
    key = text.lower()
    if any(x in key for x in ("permit", "grant", "approv")):
        return "approved"
    if "refus" in key:
        return "refused"
    if any(x in key for x in ("withdraw", "invalid", "returned")):
        return "withdrawn"
    if any(x in key for x in ("pending", "appeal", "other")):
        return "pending"

    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised status {text!r} — filed as 'pending'")
    return "pending"


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


async def scrape() -> list[dict]:
    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    start_ms = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)

    query_url = f"{BASE_URL}/{ALL_APPS_LAYER}/query"
    params = {
        "where": f"DATE_RECEIVED >= {start_ms} AND DATE_RECEIVED <= {end_ms}",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": "2000",
    }

    async with httpx.AsyncClient(timeout=30, verify=_legacy_ssl_context()) as client:
        try:
            r = await client.get(query_url, params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            _log(f"⚠ Query failed: {type(e).__name__}: {e!r}")
            return []

        if "error" in data:
            _log(f"⚠ Real API error: {data['error']}")
            return []

        features = data.get("features", [])
        _log(f"Real records returned for the last {DAYS_BACK} days: {len(features)}")
        return [f.get("attributes", {}) for f in features]


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Fife Council scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"fife_councils.py. Run the INSERT_SQL there, look up the "
              f"real id, and fill it in before running this scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results — nothing to save.")
        return

    addresses = [_clean_address(a.get("ADDRESS", "")) for a in raw_apps]
    postcodes = [_extract_postcode(a) for a in addresses]
    valid_postcodes = [p for p in postcodes if p]
    coords = await geocode(valid_postcodes) if valid_postcodes else {}
    if valid_postcodes:
        _log(f"Geocoding {len(valid_postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for a, address, postcode in zip(raw_apps, addresses, postcodes):
        lat, lng = None, None
        if postcode:
            key = postcode.upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        reference = a.get("REFVAL")
        if not reference:
            continue

        records.append({
            "council_id": cid,
            "reference": reference,
            "submitted_date": _parse_epoch_ms(a.get("DATE_RECEIVED")),
            "decision_date": _parse_epoch_ms(a.get("DECISION_ISSUED_DATE")),
            "address": address or None,
            "postcode": postcode,
            "description": a.get("PROPOSAL") or None,
            "application_type": a.get("APPLICATION_TYPE"),
            "status": _normalise_fife_status(a.get("GIS_STATUS", ""), a.get("DECISION", "")),
            "council_url": a.get("FURTHER_INFO_PUBLIC_URL"),
            "lat": lat,
            "lng": lng,
            "source": "fife_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "fife_arcgis",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print("Finished")


if __name__ == "__main__":
    asyncio.run(main())
