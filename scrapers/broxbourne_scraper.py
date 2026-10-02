#!/usr/bin/env python3
"""
PlanFind — Broxbourne Borough Council scraper (2026-09-30).

First-ever scraper for the LPAssure platform in this project —
Broxbourne and Hyndburn were both previously "never built at all".

REAL, CONFIRMED FLOW (via direct diagnostic tonight):
  1. Click the outer "Weekly / Monthly list" button (opens a panel;
     its inner Weekly/Monthly links start hidden until this is clicked).
  2. Click the inner "Monthly list" link — targeted by its exact,
     confirmed onclick handler
     (GetOnlinePlanningWeeklySearchView(false)), since a broad text
     match on "Monthly" accidentally re-clicked the outer toggle
     during diagnosis (it also contains the word "Monthly").
  3. Select a month from select#SelectedMonth (options are real month
     names, e.g. "September 2026").
  4. Check ONE of two mutually exclusive radios (same real name
     attribute, MonthlyListStatus): #ValidatedThisMonth or
     #DecidedThisMonth. Confirmed these show genuinely different
     status vocabularies — Validated: "REGISTERED"; Decided: "FINAL
     DECISION", "WITHDRAWN" — so both searches are run, not just one,
     to capture new submissions and decision outcomes separately.
  5. Click the real "Search" button — confirmed via direct diagnostic
     that the page has THREE different buttons containing the text
     "Search" ("Map search", the real one, "Refine search"); the real
     one is identified by its nearest ancestor with a real id,
     'ancWeeklyMonthlySearch'.
  6. Submitting a search replaces the whole page with results,
     confirmed to hide the original search panel — re-running the
     same 5 steps is required for a second search, not just changing
     the radio and resubmitting.

REAL, CONFIRMED RESULTS STRUCTURE: table.table-striped
.OnlinePlanningWeeklyMonthlySort, tbody#divWeeklyMonthlySearchResultsForSorting,
each row with 6 <td> cells (reference linking to a real detail page,
status, development type, description, address, date received).
Longer descriptions are truncated with a "read more" link whose
onclick attribute contains the FULL, untruncated text
(DisplaySearchDescription('full text', 'Description')) — extracted
directly from there rather than settling for the truncated version.

HONEST LIMITATION: pagination exists (79-97 results, ~20 shown per
page) but its exact mechanism wasn't directly confirmed before writing
this — built defensively with the same same-page-detection safeguard
already proven for West Northamptonshire, looking for a real "Next"
control by text.
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

BASE_URL = "https://planning.broxbourne.gov.uk"
SEARCH_URL = f"{BASE_URL}/LPAssure/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch"
COUNCIL_NAME = "Broxbourne Borough Council"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "30"))
MONTHS_BACK  = int(os.environ.get("MONTHS_BACK", "2"))  # current + N-1 previous months
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "20"))
BROXBOURNE_COUNCIL_ID = int(os.environ.get("BROXBOURNE_COUNCIL_ID", "0"))

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
    if any(x in key for x in ("regist", "pending", "awaiting")):
        return "pending"
    # Confirmed real, genuinely ambiguous status from the "Decided"
    # search — reveals a decision exists without saying which outcome.
    if "final decision" in key or "decided" in key:
        return "pending"
    # An appeal being lodged doesn't reveal its eventual outcome.
    if "appeal lodged" in key:
        return "pending"
    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        print(f"    ⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


def _parse_date(s: str) -> Optional[str]:
    """Real, confirmed format: 'DD Month YYYY' (e.g. '30 September 2026')."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def _real_month_options(n_months: int) -> list[str]:
    """Real, confirmed dropdown option format: 'September 2026'. Builds
    the current month plus n_months-1 previous ones."""
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(n_months):
        months.append(date(y, m, 1).strftime("%B %Y"))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_page(html: str) -> list[dict]:
    """Real, confirmed structure from direct diagnostic tonight:
    table.OnlinePlanningWeeklyMonthlySort,
    tbody#divWeeklyMonthlySearchResultsForSorting, 6 <td> cells per row."""
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    table = soup.find("table", class_="OnlinePlanningWeeklyMonthlySort")
    if not table:
        if not _ROW_STRUCTURE_DIAGNOSED:
            _ROW_STRUCTURE_DIAGNOSED = True
            print(f"    ⚠ ROW STRUCTURE DIAGNOSTIC: no real "
                  f"table.OnlinePlanningWeeklyMonthlySort found — real "
                  f"structure may have changed, or this search genuinely "
                  f"has zero results")
        return apps

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        ref_link = cells[0].find("a", href=True)
        reference = ""
        detail_url = None
        if ref_link:
            span = ref_link.find("span")
            reference = (span.get_text(strip=True) if span else ref_link.get_text(strip=True))
            href = ref_link.get("href")
            if href:
                detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        if not reference or len(reference) < 3:
            continue

        status_raw = cells[1].get_text(strip=True)
        dev_type = cells[2].get_text(strip=True)

        # Real, confirmed bonus: a "read more" link's onclick carries
        # the FULL, untruncated description — extracted directly
        # rather than settling for the truncated <td> text.
        description = cells[3].get_text(strip=True)
        read_more = cells[3].find("a", onclick=re.compile(r"DisplaySearchDescription"))
        if read_more:
            m = re.search(r"DisplaySearchDescription\('(.*?)',\s*'Description'\)",
                           read_more.get("onclick", ""), re.S)
            if m:
                description = m.group(1).strip()
            else:
                # Fall back to the truncated text with the "read more"
                # label stripped off, rather than keeping it glued on.
                description = re.sub(r"\s*read more\s*$", "", description).strip()

        address = cells[4].get_text(strip=True)
        date_raw = cells[5].get_text(strip=True)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": _extract_postcode(address),
            "description": description,
            "application_type": dev_type or "Planning",
            "status": _normalise_status(status_raw),
            "submitted_date": _parse_date(date_raw),
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


async def open_monthly_panel(page):
    """Real, confirmed 3-step flow to reach the month/status controls.
    Must be re-run before EACH search — confirmed via diagnostic that
    submitting a search replaces the page with results, hiding this
    panel entirely."""
    outer_toggle = page.locator("button:has-text('Weekly / Monthly')")
    await outer_toggle.click(timeout=5_000)
    await asyncio.sleep(1)

    monthly_link = page.locator("[onclick*='GetOnlinePlanningWeeklySearchView(false)']")
    await monthly_link.click(timeout=5_000, force=True)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1.5)


async def find_real_search_button(page):
    """Real, confirmed: the page has 3 buttons containing 'Search' text
    ('Map search', the real one, 'Refine search'). The real one is
    identified by its nearest ancestor with a real id,
    'ancWeeklyMonthlySearch' — confirmed via direct diagnostic, not a
    guess."""
    all_search_btns = page.locator("button", has_text="Search")
    for i in range(await all_search_btns.count()):
        btn = all_search_btns.nth(i)
        text = (await btn.text_content() or "").strip()
        parent_id = await btn.evaluate(
            "el => el.closest('[id]') ? el.closest('[id]').id : null"
        )
        if text == "Search" and parent_id == "ancWeeklyMonthlySearch":
            return btn
    return None


async def scrape_month_status(page, month_label: str, status_id: str,
                               status_name: str) -> list[dict]:
    """One real search: a given month + one of the two mutually
    exclusive status radios."""
    try:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        await open_monthly_panel(page)

        month_select = page.locator("select#SelectedMonth")
        if await month_select.count() == 0:
            print(f"    [{month_label}/{status_name}] ⚠ No real month select found")
            return []

        # REAL, CONFIRMED FIX — a production run showed the current
        # calendar month ("October 2026") genuinely isn't offered as a
        # selectable option yet (the council's system appears to only
        # add it a few days into the month, or once its first real
        # application exists) — select_option() threw a confusing
        # 5-second timeout for what's actually an expected, calm case.
        # Checking the real available options first and skipping
        # gracefully with a clear message, rather than treating this
        # as a genuine error each time it happens.
        available_months = await month_select.locator("option").all_text_contents()
        if month_label not in available_months:
            print(f"    [{month_label}/{status_name}] month not yet offered by the "
                  f"council's own system (real available months: {available_months}) "
                  f"— skipping, not an error")
            return []

        await month_select.select_option(label=month_label, timeout=5_000)

        status_radio = page.locator(f"input#{status_id}")
        await status_radio.check(timeout=5_000, force=True)

        search_btn = await find_real_search_button(page)
        if search_btn is None:
            print(f"    [{month_label}/{status_name}] ⚠ No real Search button found")
            return []

        await search_btn.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(2)
    except Exception as e:
        print(f"    [{month_label}/{status_name}] ⚠ Search failed: {type(e).__name__}: {e!r}")
        return []

    # REAL, CONFIRMED FIX — a production run showed every search
    # returning exactly 20 applications regardless of the real total
    # (September 2026 alone has 79 Validated / 97 Decided), because
    # the real pagination is a Knockout.js-driven numbered page list
    # (ul.pagination, "1 2 3..."), not a "Next" text link at all — the
    # previous selector never matched anything. A real page-size
    # dropdown (PagingParameters_PageSize, options 10/20/50) was also
    # confirmed — setting it to 50 up front cuts the number of pages
    # needed roughly in half or better, before falling back to
    # clicking real numbered page links for whatever remains.
    page_size_select = page.locator("select#PagingParameters_PageSize")
    if await page_size_select.count() > 0:
        try:
            await page_size_select.select_option(value="50", timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"    [{month_label}/{status_name}] ⚠ Could not set page size to 50: "
                  f"{type(e).__name__} — continuing with whatever size is active")

    all_apps: list[dict] = []
    seen_refs: set[str] = set()
    previous_refs: frozenset = frozenset()
    page_num = 1

    while page_num <= MAX_PAGES:
        if should_stop():
            print(f"    [{month_label}/{status_name}] ⚠ Time budget reached at page {page_num}")
            break

        html = await page.content()
        page_apps = _parse_results_page(html)

        current_refs = frozenset(a["reference"] for a in page_apps)
        if page_num > 1 and current_refs and current_refs == previous_refs:
            print(f"    [{month_label}/{status_name}] Page {page_num} identical to "
                  f"previous — pagination not advancing, stopping")
            break
        previous_refs = current_refs

        new_count = 0
        for a in page_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
                new_count += 1

        if page_num == 1:
            print(f"    [{month_label}/{status_name}] Page 1: {len(page_apps)} real applications")

        if not page_apps:
            break

        # Real, confirmed numbered pagination — click the link for the
        # specific next page number, inside the real ul.pagination
        # container, rather than a generic "Next" text match.
        next_page_link = page.locator(
            f"ul.pagination a:text-is('{page_num + 1}'), "
            f"ul.pagination span:text-is('{page_num + 1}')"
        )
        if await next_page_link.count() == 0:
            break
        try:
            await next_page_link.first.click(timeout=10_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1.5)
        except Exception:
            break
        page_num += 1

    print(f"    [{month_label}/{status_name}] Total: {len(all_apps)} real applications "
          f"across {page_num} page(s)")
    return all_apps


async def scrape() -> list[dict]:
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    months = _real_month_options(MONTHS_BACK)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        for month_label in months:
            for status_id, status_name in [
                ("ValidatedThisMonth", "Validated"),
                ("DecidedThisMonth", "Decided"),
            ]:
                if should_stop():
                    print(f"⚠ Time budget reached, stopping before {month_label}/{status_name}")
                    break
                month_apps = await scrape_month_status(page, month_label, status_id, status_name)
                for a in month_apps:
                    if a["reference"] not in seen_refs:
                        seen_refs.add(a["reference"])
                        all_apps.append(a)

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Broxbourne Borough Council scraper")
    print(f"Months back: {MONTHS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    if not BROXBOURNE_COUNCIL_ID:
        print("ERROR: BROXBOURNE_COUNCIL_ID not set.")
        sys.exit(1)

    raw_apps = await scrape()

    if not raw_apps:
        await _supa_patch_council(BROXBOURNE_COUNCIL_ID, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        print("\nNo real Broxbourne applications found this run.")
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
            "council_id": BROXBOURNE_COUNCIL_ID,
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
            "source": "broxbourne_scraper",
        })

    if fallback_count:
        print(f"No real coordinate for {fallback_count} apps (left null — no centroid fallback yet)")

    print(f"Upserting {len(records)} records with council_id={BROXBOURNE_COUNCIL_ID}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"✓ Saved {len(records)}")
        await _supa_patch_council(BROXBOURNE_COUNCIL_ID, {
            "coverage_source": "broxbourne_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {len(records) if ok else 0}")


if __name__ == "__main__":
    asyncio.run(main())
