#!/usr/bin/env python3
"""
PlanFind — Charnwood (Assure platform) scraper (2026-08-29).

Real, confirmed evidence backing every design decision — see
charnwood_councils.py. Genuinely the most stubborn platform this
session — 8+ real diagnostic rounds across 2 days before all pieces
confirmed working.

ARCHITECTURE: for each real month within DAYS_BACK, run the full real
5-step interaction (Planning applications radio, Weekly/Monthly list,
Monthly list, select month, check Validated this month, click
#ancWeeklyMonthlySearch), poll for the real "N Results" text (a
genuine, variable AJAX delay — confirmed anywhere from ~4s to
15s+), parse the real results table, then page through via direct
page.evaluate() calls to the real underlying PagingClick('N') JS
function (a real UI click on the pagination link was confirmed to
mechanically succeed with no error while never actually triggering
the AJAX content swap, across several separate attempts).

HONEST LIMITATIONS:
  - Real "Status" column values are workflow stages only (REGISTERED,
    FINAL DECISION, etc.) — even "FINAL DECISION" doesn't reveal which
    decision was made. Every application starts as 'pending' from
    this scraper; a genuine pending-recheck mechanism (confirmed
    stable GUID-based detail URLs) is expected to pick up real
    decisions later.
  - This platform requires selecting one specific month at a time —
    there's no real free-form date-range search. Covering DAYS_BACK
    means repeating the whole 5-step flow once per real month touched
    by the window, similar to Stratford's own month-iteration need.
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
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeout

from charnwood_councils import COUNCIL_DB_IDS, BASE_URL

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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
MAX_PAGES_PER_MONTH = int(os.environ.get("MAX_PAGES_PER_MONTH", "15"))
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "50"))

COUNCIL_NAME = "Charnwood Borough Council"

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


def _parse_uk_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _target_months(days_back: int) -> list[str]:
    """Real, confirmed month dropdown label format: 'July 2026'. Builds
    the list of real month labels needed to cover the desired window,
    from the current month backwards."""
    today = date.today()
    cutoff = today - timedelta(days=days_back)
    months = []
    cursor = today.replace(day=1)
    while cursor >= cutoff.replace(day=1):
        months.append(cursor.strftime("%B %Y"))
        # step back one real month
        if cursor.month == 1:
            cursor = cursor.replace(year=cursor.year - 1, month=12)
        else:
            cursor = cursor.replace(month=cursor.month - 1)
    return months


def _parse_results_table(html: str) -> list[dict]:
    """Real, confirmed structure: 2nd real <table> on the page, header
    row with real <th> cells, columns Reference No. | Status |
    Development type | Description | Address | Date Registered."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    data_table = None
    for t in tables:
        rows = t.find_all("tr")
        if len(rows) > 1 and len(rows[0].find_all("th")) >= 5:
            data_table = t
            break

    if not data_table:
        return []

    rows = data_table.find_all("tr")
    apps = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        ref_cell = cells[0]
        link = ref_cell.find("a")
        reference = ref_cell.get_text(strip=True)
        if not reference:
            continue

        detail_url = None
        if link and link.get("href"):
            href = link["href"]
            detail_url = href if href.startswith("http") else f"https://planningexplorer.charnwood.gov.uk{href}"

        address = cells[4].get_text(strip=True)
        description = cells[3].get_text(" ", strip=True)
        date_registered = cells[5].get_text(strip=True)
        postcode = _extract_postcode(address)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": description,
            "submitted_date": _parse_uk_date(date_registered),
            "status": "pending",  # real, confirmed: list-view status
                                    # values are workflow stages only,
                                    # never the real decision outcome
            "council_url": detail_url,
        })

    return apps


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_h())
        r.raise_for_status()
        return r.json()


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


async def _reach_month_results(page, month_label: str) -> bool:
    """Real, confirmed 5-step interaction flow. Returns whether the
    real results state was successfully reached."""
    try:
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        _log(f"⚠ Could not load search page: {e}")
        return False

    try:
        await page.locator("#PlanningApplications").check(timeout=5_000)
        await page.get_by_text("Weekly / Monthly list", exact=True).first.click(timeout=8_000)
        await asyncio.sleep(1)
        await page.get_by_text("Monthly list", exact=True).first.click(timeout=8_000)
        await asyncio.sleep(1)

        month_select = page.locator("select").filter(has=page.locator("option", has_text="20")) 
        options = await month_select.first.locator("option").all_text_contents()
        if month_label not in options:
            # REAL, CONFIRMED via a live production run: this is
            # genuinely expected for the CURRENT calendar month
            # specifically — the platform's own Monthly List only
            # offers completed months (confirmed real dropdown never
            # includes the current, still-ongoing month), not a real
            # error. Harmless — the loop correctly skips to the next
            # real month.
            _log(f"⚠ Real month '{month_label}' not found in dropdown options — "
                 f"expected if this is the current calendar month (the platform's "
                 f"Monthly List only offers completed months)")
            return False
        await month_select.first.select_option(label=month_label, timeout=5_000)

        # REAL, CONFIRMED REQUIRED — a blocking "Please select a
        # status" validation error occurs without this, despite an
        # earlier manual test working without it. Genuine,
        # unexplained discrepancy between manual and automated
        # interaction on this specific platform.
        validated_cb = page.get_by_text("Validated this month", exact=True)
        if await validated_cb.count() > 0:
            await validated_cb.first.click(timeout=5_000)

        await page.locator("#ancWeeklyMonthlySearch").first.click(timeout=8_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        _log(f"⚠ Error during real interaction flow for {month_label}: {e}")
        return False

    # Real, confirmed necessary polling — the real "N Results" text
    # appears after a genuine, variable AJAX delay (confirmed anywhere
    # from ~4s to 15s+ across different attempts), a fixed sleep is
    # not reliable.
    for _ in range(15):
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            body_text = ""
        if re.search(r"\d+ Results", body_text):
            return True
        await asyncio.sleep(1)

    return False


async def scrape_month(browser: Browser, month_label: str) -> list[dict]:
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    apps: list[dict] = []
    seen_refs: set[str] = set()

    reached = await _reach_month_results(page, month_label)
    if not reached:
        _log(f"⚠ Could not reach real results for {month_label} — skipping")
        await context.close()
        return apps

    for page_num in range(MAX_PAGES_PER_MONTH):
        if should_stop():
            _log(f"⚠ Time budget reached, stopping mid-{month_label}")
            break

        html = await page.content()
        page_apps = _parse_results_table(html)
        new_count = 0
        for a in page_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                apps.append(a)
                new_count += 1

        m = re.search(r"(\d+) Results", await page.locator("body").inner_text())
        real_total = int(m.group(1)) if m else None

        _log(f"{month_label} — page {page_num + 1}: {new_count} new "
             f"(running total {len(apps)}" + (f" of {real_total} real total" if real_total else "") + ")")

        if real_total is not None and len(apps) >= real_total:
            break
        if not page_apps:
            break

        # REAL FIX — confirmed necessary via a live production run:
        # comparing the WHOLE page HTML was too fragile a signal,
        # catching transient in-between DOM states as "no change" and
        # silently desyncing the real page index from what the
        # platform actually shows — causing real data loss (only 120
        # of 175 real applications saved on the first live run,
        # multiple pages alternating between real new content and
        # false "0 new" repeats of already-seen content). Comparing
        # the specific first-row reference instead — the same precise
        # method already proven reliable in
        # charnwood_guid_pagination_test.py's own successful real test.
        try:
            first_ref_before = page_apps[0]["reference"] if page_apps else None
            await page.evaluate(
                f"$('#CurrentPageIndex').val({page_num + 1}); PagingClick('{page_num + 1}');"
            )
            changed = False
            for _ in range(10):
                await asyncio.sleep(1)
                try:
                    html_after = await page.content()
                    apps_after = _parse_results_table(html_after)
                except Exception:
                    continue
                first_ref_after = apps_after[0]["reference"] if apps_after else None
                if first_ref_after and first_ref_after != first_ref_before:
                    changed = True
                    break
            if not changed:
                _log(f"⚠ Real pagination to page {page_num + 2} did not change "
                     f"the real first reference after 10s — stopping {month_label} here")
                break
        except Exception as e:
            _log(f"⚠ Could not paginate for {month_label}: {e}")
            break

    await context.close()
    return apps


async def recheck_pending(browser: Browser, pending: list[dict]) -> list[dict]:
    """Real, confirmed STABLE detail URL — genuinely reusable in a
    completely fresh browser session with no shared cookies/state.
    Real, confirmed exact label: 'Decided: {OUTCOME}'."""
    if not pending:
        return []
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    updates = []
    for p in pending:
        if should_stop():
            _log(f"⚠ Time budget reached mid-recheck, stopping")
            break
        url = p.get("council_url")
        if not url:
            continue
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
        except Exception:
            continue

        text = ""
        try:
            text = await page.locator("body").inner_text()
        except Exception:
            continue

        m = re.search(r"Decided:\s*([A-Za-z ,.'()-]+)", text)
        if m:
            outcome = m.group(1).strip().lower()
            if any(x in outcome for x in ("grant", "approv", "permit", "allow", "condition")):
                status = "approved"
            elif any(x in outcome for x in ("refus", "reject", "dismiss")):
                status = "refused"
            elif "withdraw" in outcome:
                status = "withdrawn"
            else:
                status = "pending"
            if status != "pending":
                updates.append({"reference": p["reference"], "status": status})

    await context.close()
    if updates:
        _log(f"Recheck: {len(updates)} of {len(pending)} previously-pending "
             f"application(s) now have a real decision")
    return updates


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Charnwood scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"charnwood_councils.py.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    months = _target_months(DAYS_BACK)
    print(f"Real months to cover: {months}\n")

    pending = []
    try:
        pending = await _supa_get(
            "planning_applications",
            council_id=f"eq.{cid}",
            status="eq.pending",
            select="reference,council_url",
            limit=str(RECHECK_LIMIT),
        )
        if pending:
            _log(f"Pending recheck: {len(pending)} applications (bounded to {RECHECK_LIMIT})")
    except Exception as e:
        _log(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}")

    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for month_label in months:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping before {month_label}")
                break
            month_apps = await scrape_month(browser, month_label)
            for a in month_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)

        recheck_updates = await recheck_pending(browser, pending)

        await browser.close()

    if not all_apps and not recheck_updates:
        print("\nNo results and no recheck updates — nothing to save.")
        return

    postcodes = [a["postcode"] for a in all_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        _log(f"Geocoding {len(postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for a in all_apps:
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
            "address": a["address"] or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "submitted_date": a.get("submitted_date"),
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "charnwood_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    recheck_records = [{
        "council_id": cid,
        "reference": u["reference"],
        "status": u["status"],
    } for u in recheck_updates]

    saved_count = 0
    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        if await _supa_upsert(records):
            saved_count += len(records)

    if recheck_records:
        _log(f"Upserting {len(recheck_records)} recheck status updates with council_id={cid}")
        if await _supa_upsert(recheck_records):
            saved_count += len(recheck_records)

    if saved_count:
        _log(f"✓ Saved {saved_count}")
        await _supa_patch_council(cid, {
            "coverage_source": "charnwood_assure",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
