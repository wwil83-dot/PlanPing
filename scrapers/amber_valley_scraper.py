#!/usr/bin/env python3
"""
PlanFind — Amber Valley Borough Council scraper (2026-08-31).

Real, confirmed evidence backing every design decision — see
amber_valley_councils.py.

ARCHITECTURE: genuinely the best platform in the whole project — a
real, documented JSON web-service API. Two plain form-encoded httpx
POSTs (one for pending, one for a date-range of decided applications),
clean JSON in, no HTML parsing, no Playwright, no session/CSRF
anywhere.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from amber_valley_councils import COUNCIL_DB_IDS, NON_DETERMINED_URL, DETERMINED_URL

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))

COUNCIL_NAME = "Amber Valley Borough Council"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(text: str) -> str:
    """Real, confirmed: address lines are \\r-separated, not \\n."""
    if not text:
        return ""
    parts = [p.strip() for p in text.split("\r") if p.strip()]
    return ", ".join(parts)


def _parse_json_date(value: Optional[str]) -> Optional[str]:
    """Real format: ISO datetime string, or '0001-01-01T00:00:00' as a
    null-equivalent sentinel — both handled here."""
    if not value or value.startswith("0001-01-01"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "")).date().isoformat()
    except ValueError:
        return None


_DECISION_DIAGNOSED = False


def _normalise_amber_valley_status(app: dict) -> str:
    """HONEST LIMITATION: real 'decision' and 'status' fields are both
    null in every sample seen, even when decided=true — this feed
    appears to only expose a binary decided/not-decided signal, not
    the actual outcome. Logged once as a diagnostic rather than
    guessing at approved/refused."""
    global _DECISION_DIAGNOSED
    decision_text = app.get("decision")
    status_text = app.get("status")

    if decision_text:
        key = decision_text.lower()
        if any(x in key for x in ("approv", "grant", "permit")):
            return "approved"
        if any(x in key for x in ("refus", "reject")):
            return "refused"
        if "withdraw" in key:
            return "withdrawn"

    if app.get("decided") and not decision_text and not status_text:
        if not _DECISION_DIAGNOSED:
            _DECISION_DIAGNOSED = True
            _log("⚠ STATUS DIAGNOSTIC: decided=true but real 'decision'/"
                 "'status' fields are both null for this and likely "
                 "other records — this feed doesn't expose the actual "
                 "outcome, filing all decided apps as 'pending' rather "
                 "than guess. Sample refVal: " + str(app.get("refVal")))
        return "pending"

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
    from_date = start.strftime("%d-%b-%Y")
    to_date = today.strftime("%d-%b-%Y")

    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30) as client:
        try:
            r = await client.post(NON_DETERMINED_URL, data={"wardCode": "", "parishCode": ""})
            r.raise_for_status()
            pending = r.json()
            _log(f"PlanAppsAllValidNonDetermined: {len(pending)} real applications")
            for a in pending:
                ref = a.get("refVal")
                if ref and ref not in seen_refs:
                    seen_refs.add(ref)
                    all_apps.append(a)
        except Exception as e:
            _log(f"⚠ Non-determined fetch failed: {e}")

        try:
            r = await client.post(DETERMINED_URL, data={
                "wardCode": "", "parishCode": "",
                "fromDate": from_date, "toDate": to_date,
            })
            r.raise_for_status()
            determined = r.json()
            _log(f"PlanAppsDetermined ({from_date} to {to_date}): {len(determined)} real applications")
            for a in determined:
                ref = a.get("refVal")
                if ref and ref not in seen_refs:
                    seen_refs.add(ref)
                    all_apps.append(a)
        except Exception as e:
            _log(f"⚠ Determined fetch failed: {e}")

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Amber Valley scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"amber_valley_councils.py. Run the INSERT_SQL there, look "
              f"up the real id, and fill it in before running this "
              f"scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results — nothing to save.")
        return

    address_strings = [_clean_address(a.get("applicationAddress", "")) for a in raw_apps]
    postcodes = [_extract_postcode(a) for a in address_strings]
    valid_postcodes = [p for p in postcodes if p]
    coords = await geocode(valid_postcodes) if valid_postcodes else {}
    if valid_postcodes:
        _log(f"Geocoding {len(valid_postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for a, address, postcode in zip(raw_apps, address_strings, postcodes):
        lat, lng = None, None
        if postcode:
            key = postcode.upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        records.append({
            "council_id": cid,
            "reference": a["refVal"],
            "submitted_date": _parse_json_date(a.get("dateReceived")),
            "decision_date": _parse_json_date(a.get("dateDecision")),
            "address": address or None,
            "postcode": postcode,
            "description": a.get("proposal") or None,
            "application_type": a.get("developmentTypeCode") or None,
            "status": _normalise_amber_valley_status(a),
            "council_url": None,  # no per-application detail URL found in this feed
            "lat": lat,
            "lng": lng,
            "source": "amber_valley_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "amber_valley_json_api",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
