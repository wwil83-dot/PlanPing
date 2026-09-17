#!/usr/bin/env python3
"""
PlanFind — Vale of White Horse & South Oxfordshire Weekly List CSV
scraper (2026-09-17).

REPLACES the earlier search-form based scraper — see
vowh_soxon_councils.py's module docstring for the full context on why
(the council's own search feature is confirmed broken) and the
real, confirmed CSV structure this is built from.

REAL, CONFIRMED MECHANISM (via vowh_soxon_weeklylist_diagnostic.py):
the Weekly List page has one button per week (button.getWeeklyListCSV,
data-date="DD/MM/YYYY"), a JS-triggered download — not a plain link,
so this needs a real browser click + Playwright's download capture,
not a direct HTTP request.

HONEST LIMITATION: this is a "Planning Applications Received" report
— it has no decision/status column, so every application is filed as
'pending'. The exact real format of received_complete_date and the
real values ps2_category takes were never directly confirmed beyond
the header names themselves — both are handled defensively below with
diagnostics, not assumed.
"""
import asyncio
import csv
import io
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from vowh_soxon_councils import VOWH_SOXON_COUNCILS, COUNCIL_DB_IDS

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,
    "accept_downloads": True,
}

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


_DATE_FORMAT_DIAGNOSED: set[str] = set()
_DATE_PREFIX_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")


def _parse_received_date(raw: str, council_name: str) -> Optional[date]:
    """REAL FIX (2026-09-17) — confirmed via the actual first
    production run: this field's real value stacks a validation
    status ("Valid") on top of the real date, separated by an embedded
    line break within the same CSV cell (e.g. "Valid\\r\\n09/09/2026")
    — not a malformed date, a genuinely different real structure than
    assumed. Extracts a real DD/M/YYYY-shaped date from anywhere in
    the raw value rather than requiring the whole field to be just a
    date. The prefix text itself is diagnosed once if it's ever
    something other than the one real value confirmed so far
    ("Valid") — worth knowing if a genuinely different status
    (e.g. "Invalid") ever shows up here."""
    raw = (raw or "").strip()
    if not raw:
        return None

    match = _DATE_PREFIX_RE.search(raw)
    if match:
        prefix = raw[:match.start()].strip()
        if prefix and prefix != "Valid" and prefix not in _DATE_FORMAT_DIAGNOSED:
            _DATE_FORMAT_DIAGNOSED.add(prefix)
            print(f"    [{council_name}] ⚠ DATE PREFIX DIAGNOSTIC: received_complete_date "
                  f"had an unrecognised prefix {prefix!r} (only 'Valid' confirmed "
                  f"before) — date still extracted, but this prefix's real "
                  f"meaning is unconfirmed")
        try:
            return datetime.strptime(match.group(1), "%d/%m/%Y").date()
        except ValueError:
            pass

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    if raw not in _DATE_FORMAT_DIAGNOSED:
        _DATE_FORMAT_DIAGNOSED.add(raw)
        print(f"    [{council_name}] ⚠ DATE FORMAT DIAGNOSTIC: could not parse "
              f"received_complete_date {raw!r} with any known format")
    return None


def _parse_weeklylist_csv(raw_bytes: bytes, council_name: str) -> list[dict]:
    """Real, confirmed structure via vowh_soxon_weeklylist_diagnostic.py:
    3 metadata/title lines before the real header row. Rather than
    assume a fixed line count (fragile if it ever changes slightly),
    finds the real header line by its confirmed, distinctive first
    column name, then uses csv.DictReader from there — which correctly
    handles quoted fields containing embedded newlines, unlike a naive
    line-by-line split."""
    text = raw_bytes.decode("utf-8-sig", errors="replace")  # utf-8-sig strips a real BOM if present
    lines = text.splitlines(keepends=True)

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("application_number"):
            header_idx = i
            break

    if header_idx is None:
        print(f"    [{council_name}] ⚠ CSV STRUCTURE DIAGNOSTIC: could not find "
              f"the real 'application_number' header line — real format may "
              f"have changed")
        return []

    csv_text = "".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))

    apps = []
    for row in reader:
        reference = (row.get("application_number") or "").strip()
        if not reference:
            continue

        location = (row.get("location1") or "").strip()
        proposal = (row.get("proposal1") or "").strip()
        applicant = (row.get("applicants_name") or "").strip()
        received_raw = (row.get("received_complete_date") or "").strip()
        # HONEST LIMITATION (see module docstring) — real values never
        # confirmed beyond the column name; passed through as-is
        # rather than guessed at.
        category = (row.get("ps2_category") or "").strip()

        description = proposal
        if applicant:
            description = f"{description} (Applicant: {applicant})".strip()

        apps.append({
            "reference": reference,
            "address": location or None,
            "postcode": _extract_postcode(location),
            "description": description or None,
            "application_type": category or "Planning",
            "status": "pending",  # confirmed: a "Received" report has no decision data at all
            "submitted_date": _parse_received_date(received_raw, council_name),
            "council_url": None,  # not present in this CSV export
        })

    seen_refs: set[str] = set()
    deduped = []
    for a in apps:
        if a["reference"] not in seen_refs:
            seen_refs.add(a["reference"])
            deduped.append(a)
    return deduped


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


def _log(council_name: str, msg: str) -> None:
    print(f"    [{council_name}] {msg}")


async def _click_disclaimer_if_present(page, council_name: str) -> None:
    if "Disclaimer" not in page.url:
        return
    for selector in ["button:has-text('Accept')", "input[value*='Accept' i]"]:
        try:
            loc = page.locator(selector)
            if await loc.count() > 0:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                    await loc.first.click(timeout=5_000)
                return
        except Exception:
            continue
    _log(council_name, "⚠ Could not click through disclaimer with any known selector")


async def scrape_council(browser, council_name: str, base_url: str) -> list[dict]:
    weekly_list_url = f"{base_url}/Planning/WeeklyList"
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(weekly_list_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        await _click_disclaimer_if_present(page, council_name)

        if "WeeklyList" not in page.url:
            await page.goto(weekly_list_url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
    except Exception as e:
        _log(council_name, f"⚠ Could not load real Weekly List page: {type(e).__name__}: {e!r}")
        await context.close()
        return []

    csv_buttons = await page.locator("button.getWeeklyListCSV").all()
    _log(council_name, f"Real getWeeklyListCSV buttons found: {len(csv_buttons)}")

    cutoff = date.today() - timedelta(days=DAYS_BACK)
    relevant_buttons = []
    for btn in csv_buttons:
        raw_date = await btn.get_attribute("data-date")
        try:
            btn_date = datetime.strptime(raw_date, "%d/%m/%Y").date()
        except (ValueError, TypeError):
            continue
        if btn_date >= cutoff:
            relevant_buttons.append((btn_date, btn))

    _log(council_name, f"Real buttons within the last {DAYS_BACK} days: {len(relevant_buttons)}")

    for btn_date, btn in relevant_buttons:
        if should_stop():
            _log(council_name, f"⚠ Time budget reached, stopping at week of {btn_date}")
            break
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await btn.click(timeout=5_000)
            download = await download_info.value
            raw_bytes = b""
            temp_path = await download.path()
            if temp_path:
                with open(temp_path, "rb") as f:
                    raw_bytes = f.read()
        except Exception as e:
            _log(council_name, f"⚠ Could not download week of {btn_date}: {type(e).__name__}: {e}")
            continue

        week_apps = _parse_weeklylist_csv(raw_bytes, council_name)
        new_count = 0
        for a in week_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
                new_count += 1
        _log(council_name, f"Week of {btn_date}: {new_count} new "
             f"(running total {len(all_apps)})")

    await context.close()
    return all_apps


async def process_council(browser, council_name: str, base_url: str) -> int:
    cid = COUNCIL_DB_IDS[council_name]
    print(f"\n[{council_name}] (council_id={cid})")

    try:
        raw_apps = await scrape_council(browser, council_name, base_url)
    except Exception as e:
        print(f"    [{council_name}] ✗ Error: {e}")
        return 0

    if not raw_apps:
        await _supa_patch_council(cid, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        return 0

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        print(f"    [{council_name}] Geocoding {len(postcodes)} postcodes…")

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
            "address": a.get("address"),
            "postcode": a.get("postcode"),
            "description": a.get("description"),
            "application_type": a.get("application_type"),
            "status": a["status"],
            "submitted_date": a["submitted_date"].isoformat() if a.get("submitted_date") else None,
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "vowh_soxon_weeklylist_scraper",
        })

    if fallback_count:
        print(f"    [{council_name}] Council centroid fallback for {fallback_count} apps")

    print(f"    [{council_name}] Upserting {len(records)} records with council_id={cid}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"    [{council_name}] ✓ Saved {len(records)}")
        await _supa_patch_council(cid, {
            "coverage_source": "vowh_soxon_weeklylist_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })
    return len(records) if ok else 0


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Vale of White Horse / "
          f"South Oxfordshire Weekly List scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    total = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for council_name, base_url in VOWH_SOXON_COUNCILS:
            total += await process_council(browser, council_name, base_url)

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")


if __name__ == "__main__":
    asyncio.run(main())
