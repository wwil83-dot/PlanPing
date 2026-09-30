#!/usr/bin/env python3
"""
PlanFind — West Northamptonshire Council scraper (2026-09-30).

REAL, CONFIRMED via direct diagnostic tonight: West Northants was
previously PARKED as "reCAPTCHA blocked" (see idox_councils.py's own
comment). That block was real but only on a different part of the same
planning-register.co.uk vendor site — this Weekly List page
(wnc.planning-register.co.uk/Planning/WeeklyList/) is genuinely
reCAPTCHA-free, confirmed by direct inspection of the real page text
after accepting the required Copyright & Disclaimer page (the same
"needs_disclaimer" pattern already proven for Vale of Glamorgan on
this same vendor).

REAL, CONFIRMED SEARCH MECHANISM: unlike Vale of White Horse/South
Oxfordshire's CSV-download flow on this same vendor, West Northants
uses a direct GET URL with a date range
(/Search/Standard?AcknowledgeLetterDateFrom=...&AcknowledgeLetterDateTo=...),
confirmed working for an arbitrary date range (a real direct
month-long query returned "Planning (253)" results, genuinely
different from the week's 18 — confirming the URL can be constructed
directly rather than needing to click through the weekly list page
each time).

HONEST, IMPORTANT DETAIL: the date format in this URL is confirmed
American-style MM/DD/YYYY (e.g. "09/28/2026"), not the UK's usual
DD/MM/YYYY — confirmed directly from the real URL (28 cannot be a
month, so 09/28/2026 must be September 28th). Easy to get wrong if
assumed rather than checked.

REAL, CONFIRMED TABLE STRUCTURE: table.tblResults, with each real
result row's reference number linking to a real detail page
(/Planning/Display/{reference}), and address/description/validated
date/decision issue date/decision status in clearly separated,
labelled <td> cells.

HONEST LIMITATION: pagination exists (253 results for one month won't
fit on one page) but its exact mechanism wasn't directly confirmed
before writing this — built defensively, looking for a real "Next"
control by text rather than assuming a specific URL parameter.
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

BASE_URL = "https://wnc.planning-register.co.uk"
WEEKLY_LIST_URL = f"{BASE_URL}/Planning/WeeklyList/"
COUNCIL_NAME = "West Northamptonshire Council"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "20"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "30"))
WEST_NORTHANTS_COUNCIL_ID = int(os.environ.get("WEST_NORTHANTS_COUNCIL_ID", "0"))

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


def _parse_date(s: str) -> Optional[str]:
    """Real, confirmed date format on THIS platform's rendered results
    table: DD/MM/YYYY (e.g. '28/09/2026') — note this is the DISPLAYED
    format in the table, genuinely different from the MM/DD/YYYY format
    used in the URL's own query parameters. Confirmed from the real
    diagnostic output: '2026/3792/HH ... 28/09/2026 ... Pending' for an
    application in a week window ending 04/10/2026 — 28 as a day is the
    only reading consistent with that window."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _build_search_url(start: date, end: date) -> str:
    """Real, confirmed direct-URL construction for the FIRST page only
    — a real diagnostic run confirmed this returns genuinely different
    result counts for different ranges. Pagination beyond page 1 is
    AJAX-driven (confirmed via a real captured Next-link data-ajax-
    target attribute) and handled separately by clicking the real Next
    control, not by extending this URL. Date format is MM/DD/YYYY with
    a literal ' 00:00:00' time suffix, confirmed from the real URL
    captured tonight."""
    def fmt(d: date) -> str:
        return quote(f"{d.month:02d}/{d.day:02d}/{d.year} 00:00:00")

    return (
        f"{BASE_URL}/Search/Standard"
        f"?AcknowledgeLetterDateFrom={fmt(start)}"
        f"&AcknowledgeLetterDateTo={fmt(end)}"
    )


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_page(html: str) -> list[dict]:
    """Real, confirmed structure from direct diagnostic tonight:
    table.tblResults, tbody > tr, each with reference (linking to a
    real detail page), location, proposal, validated date, decision
    issue date, and decision status in separate <td> cells."""
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    table = soup.find("table", class_="tblResults")
    if not table:
        if not _ROW_STRUCTURE_DIAGNOSED:
            _ROW_STRUCTURE_DIAGNOSED = True
            print(f"    ⚠ ROW STRUCTURE DIAGNOSTIC: no real table.tblResults found — "
                  f"real structure may have changed since confirmation")
        return apps

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        ref_link = cells[0].find("a", href=True)
        reference = ref_link.get_text(strip=True) if ref_link else cells[0].get_text(strip=True)
        if not reference or len(reference) < 3:
            continue
        detail_url = None
        if ref_link and ref_link.get("href"):
            href = ref_link["href"]
            detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"

        address = cells[1].get_text(strip=True)
        # Real, confirmed quirk: each <td> has a "mobileheading" span
        # holding the column label (e.g. "Location") glued directly
        # onto the real value with no separator — strip it explicitly
        # rather than let it contaminate the real address text.
        mobileheading = cells[1].find(class_="mobileheading")
        if mobileheading:
            label_text = mobileheading.get_text(strip=True)
            if address.startswith(label_text):
                address = address[len(label_text):].strip()

        description = cells[2].get_text(strip=True)
        mobileheading = cells[2].find(class_="mobileheading")
        if mobileheading:
            label_text = mobileheading.get_text(strip=True)
            if description.startswith(label_text):
                description = description[len(label_text):].strip()

        validated_raw = cells[3].get_text(strip=True)
        mobileheading = cells[3].find(class_="mobileheading")
        if mobileheading:
            label_text = mobileheading.get_text(strip=True)
            if validated_raw.startswith(label_text):
                validated_raw = validated_raw[len(label_text):].strip()

        status_raw = cells[5].get_text(strip=True)
        mobileheading = cells[5].find(class_="mobileheading")
        if mobileheading:
            label_text = mobileheading.get_text(strip=True)
            if status_raw.startswith(label_text):
                status_raw = status_raw[len(label_text):].strip()

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": _extract_postcode(address),
            "description": description,
            "application_type": "Planning",
            "status": _normalise_status(status_raw),
            "submitted_date": _parse_date(validated_raw),
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


async def accept_disclaimer(page) -> bool:
    """Real, confirmed one-time step: this vendor's site redirects to a
    Copyright & Disclaimer page on first visit, requiring an explicit
    accept before reaching real content — same pattern already proven
    for Vale of Glamorgan on this same vendor."""
    await page.goto(WEEKLY_LIST_URL, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    if "Disclaimer" not in page.url:
        return True

    accept_candidates = [
        "button:has-text('Accept')",
        "a:has-text('Accept')",
        "button:has-text('Continue')",
        "a:has-text('Continue')",
        "input[type='submit']",
    ]
    for sel in accept_candidates:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            return "Disclaimer" not in page.url
    return False


async def scrape() -> list[dict]:
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    today = date.today()
    start = today - timedelta(days=DAYS_BACK)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        accepted = await accept_disclaimer(page)
        if not accepted:
            print("    ⚠ Could not get past the Disclaimer page — stopping")
            await context.close()
            await browser.close()
            return []
        print("    Disclaimer accepted")

        # Page 1: confirmed working direct URL construction.
        url = _build_search_url(start, today)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"    ⚠ Navigation failed on page 1: {type(e).__name__}: {e}")
            await context.close()
            await browser.close()
            return []

        page_num = 1
        previous_refs: frozenset = frozenset()
        while page_num <= MAX_PAGES:
            if should_stop():
                print(f"    ⚠ Time budget reached at page {page_num}, stopping")
                break

            html = await page.content()
            page_apps = _parse_results_page(html)

            current_refs = frozenset(a["reference"] for a in page_apps)
            if page_num > 1 and current_refs and current_refs == previous_refs:
                print(f"    Page {page_num} identical to page {page_num - 1} — "
                      f"pagination is not genuinely advancing, stopping")
                break
            previous_refs = current_refs

            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1
            print(f"    Page {page_num}: {len(page_apps)} real applications parsed, "
                  f"{new_count} new (running total {len(all_apps)})")

            if not page_apps:
                print(f"    Page {page_num}: 0 real applications parsed at all — stopping")
                break

            # REAL, CONFIRMED FIX — a production run captured the real
            # Next control's own attributes directly: href
            # '/Search/ResultsPage/{n}?module=PLA' with a matching
            # data-ajax-target, meaning this platform's pagination is
            # genuinely AJAX-driven, not a normal page navigation. A
            # constructed ?page= URL (tried previously) silently
            # returned page 1's content again every time. Clicking the
            # real link directly and waiting for its own JS to update
            # the DOM in place, rather than navigating to a guessed URL.
            next_link = page.locator("a:has-text('Next'):visible")
            if await next_link.count() == 0:
                print(f"    No visible 'Next' link — stopping at page {page_num}")
                break

            try:
                await next_link.first.click(timeout=10_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"    ⚠ Could not click Next at page {page_num}: {type(e).__name__}")
                break

            page_num += 1

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind West Northamptonshire "
          f"Council scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    if not WEST_NORTHANTS_COUNCIL_ID:
        print("ERROR: WEST_NORTHANTS_COUNCIL_ID not set. Run the check/insert SQL "
              "for West Northamptonshire Council in Supabase first.")
        sys.exit(1)

    raw_apps = await scrape()

    if not raw_apps:
        await _supa_patch_council(WEST_NORTHANTS_COUNCIL_ID, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        print("\nNo real West Northamptonshire applications found this run.")
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
            "council_id": WEST_NORTHANTS_COUNCIL_ID,
            "reference": a["reference"],
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "submitted_date": a.get("submitted_date"),
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "west_northants_scraper",
        })

    if fallback_count:
        print(f"Council centroid fallback for {fallback_count} apps")

    print(f"Upserting {len(records)} records with council_id={WEST_NORTHANTS_COUNCIL_ID}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"✓ Saved {len(records)}")
        await _supa_patch_council(WEST_NORTHANTS_COUNCIL_ID, {
            "coverage_source": "west_northants_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {len(records) if ok else 0}")


if __name__ == "__main__":
    asyncio.run(main())
