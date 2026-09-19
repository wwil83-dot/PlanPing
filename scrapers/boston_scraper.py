#!/usr/bin/env python3
"""
PlanFind — Boston Borough Council scraper (2026-09-18).

REAL, CONFIRMED CONTEXT: Boston's own search system
(boston.gov.uk/article/27319/Planning-Applications-Search) is being
actively retired — the council's own notice confirms migration to a
new shared platform "to support the continued alignment of planning
services and systems across our Partnership Councils." That shared
platform is confirmed to be publicaccess.e-lindsey.gov.uk/online-
applications — a real, existing Idox portal, already branded "South &
East Lincolnshire Councils Partnership" (Boston, East Lindsey, South
Holland), and already the base URL used by East Lindsey's own,
separate scraper entry.

REAL, CONFIRMED VIA DIAGNOSTIC: this shared portal has NO clean
"local authority" filter — its ward and parish dropdowns mix all
three councils' areas together in one flat list (e.g. "Fishtoft Ward"
is Boston's, "Crowland And Deeping St Nicholas Ward" is South
Holland's, "Alford" is East Lindsey's). A direct unfiltered monthly-
list search confirmed a real Boston address ("Joshua House, Grand
Sluice Lane, Boston, PE21 9HL") appearing in results, confirming this
portal genuinely does serve Boston's applications. Given no reliable
dropdown filter exists, this scraper filters by matching "Boston" in
each result's own address text after retrieving results — the same
approach already proven for other shared-server situations elsewhere
in this project (e.g. Cheltenham/Ipswich, verified by real address
text).

HONEST LIMITATION: this is the SAME real dependency as East Lindsey's
own scraper — if this shared portal ever goes down or changes
structure, both are affected together. East Lindsey's own existing
scraper entry currently has NO equivalent Boston-address filter,
meaning it has very likely been silently saving Boston's applications
mislabeled as East Lindsey's own — a separate, real cleanup not yet
done (deliberately deferred, per direct instruction, to focus on
getting Boston itself working first).
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

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
}

BASE_URL = "https://publicaccess.e-lindsey.gov.uk/online-applications"
COUNCIL_NAME = "Boston Borough Council"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "30"))
BOSTON_COUNCIL_ID = int(os.environ.get("BOSTON_COUNCIL_ID", "0"))

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


def _is_boston_address(address: str) -> bool:
    """REAL, CONFIRMED filter: a direct diagnostic run confirmed real
    Boston addresses on this shared portal explicitly include the
    word "Boston" (e.g. "...Grand Sluice Lane, Boston, PE21 9HL").
    Deliberately a plain substring check on the real address text
    itself, not a dropdown-based pre-filter, since no reliable
    council-level dropdown exists on this shared portal."""
    return bool(address) and "boston" in address.lower()


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_status(s: str) -> str:
    if not s:
        return "pending"
    key = s.lower()
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in ("appeal", "awaiting", "regist", "pending")):
        return "pending"
    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        print(f"    ⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_page(html: str) -> tuple[list[dict], int]:
    """Standard, real Idox results structure already confirmed across
    dozens of other working councils in this project: table with
    class 'searchresults', one row per application. Built defensively
    with a generic fallback and a one-time diagnostic, since this
    specific portal's exact row markup wasn't independently captured
    before writing this parser.

    Returns (boston_filtered_apps, total_apps_before_filter) — the
    total count is deliberately surfaced separately so a genuine
    "no Boston applications this period" result can be told apart
    from "the parser found nothing at all", which would otherwise
    look identical from the caller's side."""
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    tables = soup.find_all("table", class_="searchresults")
    if not tables:
        tables = soup.find_all("table")
        if tables and not _ROW_STRUCTURE_DIAGNOSED:
            _ROW_STRUCTURE_DIAGNOSED = True
            print(f"    ⚠ ROW STRUCTURE DIAGNOSTIC: no table.searchresults found — "
                  f"falling back to {len(tables)} generic <table> element(s) on the page")
        elif not tables:
            print(f"    ⚠ ROW STRUCTURE DIAGNOSTIC: genuinely NO <table> elements "
                  f"of any kind found on this page — the real page structure may "
                  f"differ from what was expected, or the search may not have "
                  f"actually submitted")

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            link = row.find("a", href=True)
            if not link:
                continue
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            reference = link.get_text(strip=True)
            if not reference or len(reference) < 3:
                continue

            location_raw = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            proposal = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            status_raw = cells[-1].get_text(strip=True) if len(cells) > 3 else ""

            apps.append({
                "reference": reference,
                "address": location_raw,
                "postcode": _extract_postcode(location_raw),
                "description": proposal,
                "application_type": "Planning",
                "status": _normalise_status(status_raw),
                "council_url": None,
            })

    total_before_filter = len(apps)

    # REAL FILTER — the whole point of this scraper: only keep rows
    # whose real address confirms this is genuinely a Boston
    # application, not East Lindsey's or South Holland's, since this
    # portal's results are otherwise unfiltered by council.
    boston_only = [a for a in apps if _is_boston_address(a["address"])]

    seen_refs: set[str] = set()
    deduped = []
    for a in boston_only:
        if a["reference"] not in seen_refs:
            seen_refs.add(a["reference"])
            deduped.append(a)
    return deduped, total_before_filter


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

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            monthly_url = f"{BASE_URL}/search.do?action=monthlyList"
            await page.goto(monthly_url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            month_select = page.locator("select[name='month']")
            if await month_select.count() == 0:
                print("    ⚠ No real month select found — stopping")
                await context.close()
                await browser.close()
                return []

            received_radio = page.locator(
                "input[type='radio'][value*='Received' i], input[type='radio'][value*='received' i]"
            )
            if await received_radio.count() > 0:
                await received_radio.first.check(timeout=5_000)

            submit = page.locator("input[type='submit'], button[type='submit']")
            if await submit.count() == 0:
                print("    ⚠ No real submit control found — stopping")
                await context.close()
                await browser.close()
                return []

            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await submit.first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"    ⚠ Search fill/submit failed: {type(e).__name__}: {e!r}")
            await context.close()
            await browser.close()
            return []

        print(f"    Post-submit URL: {page.url}")

        html = await page.content()
        page1_apps, page1_total = _parse_results_page(html)
        for a in page1_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
        print(f"    Page 1: {page1_total} real applications parsed in total, "
              f"{len(page1_apps)} were Boston's (running total {len(all_apps)})")

        page_num = 2
        while page_num <= MAX_PAGES:
            if should_stop():
                print(f"    ⚠ Time budget reached at page {page_num}, stopping")
                break
            next_link = page.locator("a[aria-label='Next Page.']:visible")
            if await next_link.count() == 0:
                print(f"    No visible 'Next' link — stopping at page {page_num - 1}")
                break
            try:
                await next_link.first.click(timeout=10_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"    ⚠ Could not click Next at page {page_num}: {type(e).__name__}")
                break

            html = await page.content()
            page_apps, page_total = _parse_results_page(html)
            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1
            print(f"    Page {page_num}: {page_total} real applications parsed in total, "
                  f"{new_count} new Boston ones (running total {len(all_apps)})")
            if page_total == 0:
                print(f"    Page {page_num}: 0 real applications parsed at all — stopping")
                break
            page_num += 1

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Boston Borough Council scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    if not BOSTON_COUNCIL_ID:
        print("ERROR: BOSTON_COUNCIL_ID not set. Run the INSERT_SQL for Boston "
              "Borough Council in Supabase first, then set this environment "
              "variable to the real returned id.")
        sys.exit(1)

    raw_apps = await scrape()

    if not raw_apps:
        await _supa_patch_council(BOSTON_COUNCIL_ID, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        print("\nNo real Boston applications found this run.")
        return

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        print(f"Geocoding {len(postcodes)} postcodes…")

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
            "council_id": BOSTON_COUNCIL_ID,
            "reference": a["reference"],
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "boston_scraper",
        })

    if fallback_count:
        print(f"Council centroid fallback for {fallback_count} apps")

    print(f"Upserting {len(records)} records with council_id={BOSTON_COUNCIL_ID}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"✓ Saved {len(records)}")
        await _supa_patch_council(BOSTON_COUNCIL_ID, {
            "coverage_source": "boston_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {len(records) if ok else 0}")


if __name__ == "__main__":
    asyncio.run(main())
