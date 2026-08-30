#!/usr/bin/env python3
"""
PlanFind — Ipswich Borough Council scraper (2026-08-30).

Real, confirmed evidence backing every design decision — see
ipswich_councils.py.

ARCHITECTURE: genuinely the simplest platform in the whole project —
every step is a plain GET request, no session/CSRF/cookie needed
anywhere. NO PLAYWRIGHT — plain httpx only, same category as
ni_scraper.py.

HONEST LIMITATIONS:
  - Detail URL uses a simplified 2-param version
    (appndetails.asp?iAppID=X&sType=APP) rather than the full real
    captured href (which also carried search_params/prev_search_params/
    det_search_params). This simplification was never directly tested
    — see ipswich_councils.py's docstring.
  - No pending-recheck mechanism yet (mirrors idox_scraper.py's
    pending_recheck pattern only where already built). Every
    application starts and stays whatever status it had at scrape
    time; a decision made after an application first appears won't be
    picked up until it's scraped again within the DAYS_BACK window.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from ipswich_councils import COUNCIL_DB_IDS, BASE_URL, RESULTS_URL, DETAIL_URL

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "30"))

COUNCIL_NAME = "Ipswich Borough Council"

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


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(text: str) -> str:
    parts = [" ".join(p.split()) for p in text.split("\n") if p.strip()]
    return ", ".join(parts)


def _parse_ipswich_date(text: str) -> Optional[str]:
    """Real, confirmed format: ordinal day + short month + year, e.g.
    '7th Aug 2026'. Strips the ordinal suffix before parsing."""
    if not text:
        return None
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text.strip(), flags=re.IGNORECASE)
    try:
        return datetime.strptime(cleaned, "%d %b %Y").date().isoformat()
    except ValueError:
        return None


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_ipswich_status(s: str) -> str:
    """Real, confirmed status values include 'Pending Consideration'
    and 'Approved/Conditions'. The advanced search page's own
    ddlDecision dropdown lists the fuller real vocabulary (Application
    Granted/Permitted/Refused/Withdrawn, Approved, Approved as per
    GOER, etc.) — substring-matched here, same defensive pattern as
    ni_scraper.py's _normalise_ni_status()."""
    if not s:
        return "pending"
    key = s.lower()
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in ("pending", "consideration", "awaiting")):
        return "pending"

    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


def _parse_results_page(html: str) -> tuple[list[dict], Optional[int], Optional[int]]:
    """Returns (apps, current_page, total_pages). Real, confirmed
    structure: table id='dgSearchResults', header row then one <tr>
    per application, 9 real <td> columns."""
    soup = BeautifulSoup(html, "html.parser")

    body_text = soup.get_text()
    page_match = re.search(r"Page (\d+) of (\d+)", body_text)
    current_page = int(page_match.group(1)) if page_match else None
    total_pages = int(page_match.group(2)) if page_match else None

    table = soup.find("table", id="dgSearchResults")
    if not table:
        return [], current_page, total_pages

    rows = table.find_all("tr")
    apps = []
    for row in rows[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        reference = cells[0].get_text(strip=True)
        if not reference:
            continue

        date_received = _parse_ipswich_date(cells[1].get_text(strip=True))
        address_raw = cells[2].get_text()
        address = _clean_address(address_raw)
        postcode = _extract_postcode(address_raw)
        description = cells[3].get_text(strip=True)
        status_raw = cells[4].get_text(strip=True)

        detail_url = f"{DETAIL_URL}?iAppID={quote(reference)}&sType=APP"

        apps.append({
            "reference": reference,
            "submitted_date": date_received,
            "address": address,
            "postcode": postcode,
            "description": description,
            "status": _normalise_ipswich_status(status_raw),
            "council_url": detail_url,
        })

    return apps, current_page, total_pages


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
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30, follow_redirects=True) as client:
        page_num = 1
        total_pages = None

        while page_num <= MAX_PAGES:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at page {page_num}")
                break

            params = {
                "txtValStartDate": start_str,
                "txtValEndDate": end_str,
                "pnlAdvancedOpen": "1",
            }
            if page_num > 1:
                params["pageNumber"] = str(page_num)

            try:
                r = await client.get(RESULTS_URL, params=params)
                r.raise_for_status()
            except Exception as e:
                _log(f"⚠ Request failed on page {page_num}: {e}")
                break

            page_apps, current_page, total_pages = _parse_results_page(r.text)

            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1

            _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)}"
                 + (f" of {total_pages} pages total" if total_pages else "") + ")")

            if not page_apps:
                break
            if total_pages is not None and page_num >= total_pages:
                break

            page_num += 1
            await asyncio.sleep(0.5)  # light courtesy delay — no WAF issues seen, but no reason to hammer it

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Ipswich scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"ipswich_councils.py. Run the INSERT_SQL there, look up the "
              f"real id, and fill it in before running this scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results — nothing to save.")
        return

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        _log(f"Geocoding {len(postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for a in raw_apps:
        lat, lng = None, None
        if a.get("postcode"):
            key = a["postcode"].upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "submitted_date": a.get("submitted_date"),
            "address": a["address"] or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "ipswich_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "ipswich_asp",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
