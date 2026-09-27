#!/usr/bin/env python3
"""
PlanFind — Boston Borough Council scraper (2026-09-27, ward-based rebuild).

REPLACES the earlier address-text-filtered version. That approach was
confirmed genuinely unreliable: UK postal towns and local government
districts frequently don't align (Stickney and Stickford both have
"Boston" as their postal town but are confirmed, via Wikipedia and
Lincolnshire council sources, to actually sit in East Lindsey district
— a real, direct example of the exact failure mode this rebuild
avoids).

REAL, CONFIRMED FIX: filters by real ward instead, using Boston
Borough Council's own 15 real wards (sourced from the council's
official ward poster at democracy.boston.gov.uk and real election
records), precisely matched against this shared portal's own ward
dropdown values:
  Coastal=COAS, Fenside=FENS, Fishtoft=FISH, Five Village=FIVE,
  Kirton And Frampton=KIFR, Old Leake And Wrangle=OLWR, Skirbeck=SKIR,
  St Thomas'=STTO, Staniland=STAN, Station=STAT,
  Swineshead And Holland Fen=SWHF, Trinity Ward Boston=TRINB,
  West=WEST, Witham=WITM, Wyberton=WYBE.
Two genuine false positives were confirmed and excluded during
matching: "Moulton, Weston And Cowbit Ward" (a South Holland ward,
matched only by a stray "west" substring inside "Weston"), and the
plain "Trinity Ward" without the "Boston" suffix (a different
council's ward — the "Boston" suffix on TRINB is the portal's own way
of disambiguating a real name collision between two councils' wards).

REAL, CONFIRMED SUBMISSION FLOW: the advanced search's ward field
alone triggers a real "Too many results found. Please enter some more
parameters." validation error when no date range is given (it matches
the portal's entire history, not just recent applications). Real date
field names confirmed via direct inspection: date(applicationReceivedStart)
and date(applicationReceivedEnd) — NOT the "from"/"to" naming assumed
at first, which caused two earlier false-empty diagnostic runs.
Confirmed: ward + this date range together clears the validation
cleanly with no error.

Results parsing reuses the exact structure already confirmed for this
same portal's monthly list earlier tonight (ul#searchresults >
li.searchresult, with p.address / p.metaInfo / .badge-status .value /
a.summaryLink), since it's the same underlying platform regardless of
which search type was used to reach the results page.
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

BOSTON_WARDS = [
    ("Coastal", "COAS"),
    ("Fenside", "FENS"),
    ("Fishtoft", "FISH"),
    ("Five Village", "FIVE"),
    ("Kirton And Frampton", "KIFR"),
    ("Old Leake And Wrangle", "OLWR"),
    ("Skirbeck", "SKIR"),
    ("St Thomas'", "STTO"),
    ("Staniland", "STAN"),
    ("Station", "STAT"),
    ("Swineshead And Holland Fen", "SWHF"),
    ("Trinity (Boston)", "TRINB"),
    ("West", "WEST"),
    ("Witham", "WITM"),
    ("Wyberton", "WYBE"),
]

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
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
    if "decided" in key:
        return "pending"
    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        print(f"    ⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_page(html: str) -> list[dict]:
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    results_container = soup.find("ul", id="searchresults")
    items = results_container.find_all("li", class_="searchresult") if results_container else []

    if not items and not _ROW_STRUCTURE_DIAGNOSED:
        _ROW_STRUCTURE_DIAGNOSED = True
        print(f"    ⚠ ROW STRUCTURE DIAGNOSTIC: no real ul#searchresults > "
              f"li.searchresult items found — real structure may have changed "
              f"since confirmation")

    for item in items:
        summary_link = item.find("a", class_="summaryLink")
        if not summary_link:
            continue

        href = summary_link.get("href")
        detail_url = None
        if href:
            detail_url = href if href.startswith("http") else f"{BASE_URL.rsplit('/', 1)[0]}{href}"

        desc_div = summary_link.find("div", class_="summaryLinkTextClamp")
        description = desc_div.get_text(strip=True) if desc_div else ""

        address_p = item.find("p", class_="address")
        address = address_p.get_text(strip=True) if address_p else ""

        status_value = item.select_one(".badge-status .value")
        status_raw = status_value.get_text(strip=True) if status_value else ""

        meta_info = item.find("p", class_="metaInfo")
        reference = ""
        if meta_info:
            meta_text = meta_info.get_text(" ", strip=True)
            for part in meta_text.split("·"):
                part = part.strip()
                if part.lower().startswith("ref"):
                    reference = part.split(":", 1)[1].strip() if ":" in part else ""
                    break

        if not reference or len(reference) < 3:
            continue

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": _extract_postcode(address),
            "description": description,
            "application_type": "Planning",
            "status": _normalise_status(status_raw),
            "council_url": detail_url,
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


async def scrape_ward(page, ward_name: str, ward_code: str,
                       start_str: str, end_str: str) -> list[dict]:
    advanced_url = f"{BASE_URL}/search.do?action=advanced"
    try:
        await page.goto(advanced_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        ward_select = page.locator("select[name='searchCriteria.ward']")
        if await ward_select.count() == 0:
            print(f"    [{ward_name}] ⚠ No real ward select found — stopping this ward")
            return []
        await ward_select.select_option(value=ward_code)

        await page.fill("input[name='date(applicationReceivedStart)']", start_str, timeout=5_000)
        await page.fill("input[name='date(applicationReceivedEnd)']", end_str, timeout=5_000)

        submit = page.locator("input[type='submit'], button[type='submit']")
        if await submit.count() == 0:
            print(f"    [{ward_name}] ⚠ No real submit control found — stopping this ward")
            return []

        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit.first.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"    [{ward_name}] ⚠ Search fill/submit failed: {type(e).__name__}: {e!r}")
        return []

    error_like = page.locator("[class*='error' i]")
    if await error_like.count() > 0:
        error_text = (await error_like.first.text_content() or "").strip()
        if error_text:
            print(f"    [{ward_name}] ⚠ Real validation message: {error_text!r}")
            return []

    html = await page.content()
    apps = _parse_results_page(html)
    print(f"    [{ward_name}] {len(apps)} real applications found")
    return apps


async def scrape() -> list[dict]:
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        for ward_name, ward_code in BOSTON_WARDS:
            if should_stop():
                print(f"⚠ Time budget reached, stopping before ward {ward_name}")
                break
            ward_apps = await scrape_ward(page, ward_name, ward_code, start_str, end_str)
            for a in ward_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Boston Borough Council scraper "
          f"(ward-based)")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    if not BOSTON_COUNCIL_ID:
        print("ERROR: BOSTON_COUNCIL_ID not set.")
        sys.exit(1)

    raw_apps = await scrape()

    if not raw_apps:
        await _supa_patch_council(BOSTON_COUNCIL_ID, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        print("\nNo real Boston applications found across any ward this run.")
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
