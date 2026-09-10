#!/usr/bin/env python3
"""
PlanFind — Telford and Wrekin Council scraper (2026-09-10, rebuilt).

REBUILT after a real, confirmed diagnostic trail (4 rounds) resolved
the aspxerrorpath redirect that blocked every prior run:

  1. Round 1 ruled out a simple format issue by testing page.fill()
     with a well-formed date — still failed, but confirmed the field
     genuinely has a jQuery UI datepicker attached (class="hasDatepicker").
  2. Round 2 tried real clicks through the calendar's own popup —
     initially mis-navigated (the widget uses changeMonth/changeYear
     <select> dropdowns, not plain prev/next text), fixed, then
     succeeded.
  3. Round 3 found the REAL root cause: the calendar widget's own
     value comes out DASH-formatted ('11-08-2026'), not the
     SLASH-formatted ('11/08/2026') string the original scraper sent.
     A plain page.fill() with dashes, no calendar interaction needed
     at all, was confirmed sufficient on its own — the "datepicker
     widget state" theory was a red herring; it was always just the
     wrong date string format being submitted.
  4. Round 4 found a real, confirmed data-completeness gap: results
     are split across three second-level status tabs — Received (0),
     REGISTERED (58, the DEFAULT tab — this is all the original
     scraper's generic parser ever saw), and DETERMINED (85 — never
     seen at all under the old code, a genuinely silent gap, not a
     crash). Also confirmed the page-size dropdown (10/25/50/100)
     needs an explicit separate submit button click (id
     SelectPageCountTop) to take effect — selecting 100 there
     comfortably fits every real count seen so far (58, 85, 0) on a
     single page, avoiding "Next"-click pagination entirely.

HONEST LIMITATION: the list view's confirmed columns (Application
number, Date valid, Site address, Description of proposal) don't
include the actual decision outcome for Determined applications —
just that a decision exists, not whether it was approved/refused/etc.
Every application from both tabs is saved as 'pending' for now, same
principle as this project's other scrapers when the real decision text
isn't yet confirmed — visiting each Determined application's own
detail page would be needed to get the real outcome, not yet built
here. Also: if any tab's real count ever exceeds 100 (none has so
far), items beyond the first 100 would be missed — worth watching via
the DETERMINED_UPPER_BOUND-style honest count logging below, same
principle as several other scrapers in this project flagging their own
known ceilings.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
import httpx
from bs4 import BeautifulSoup

from telford_councils import COUNCIL_DB_IDS, SEARCH_URL

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

COUNCIL_NAME = "Telford and Wrekin Council"
BASE_URL = "https://secure.telford.gov.uk"

DATE_FROM_SEL = "#ctl00_ContentPlaceHolder1_DCdatefrom"
DATE_TO_SEL = "#ctl00_ContentPlaceHolder1_DCdateto"
SUBMIT_SEL = "#ctl00_ContentPlaceHolder1_btnSearchPlanningDetails"
PAGE_SIZE_SELECT_SEL = "#ctl00_ContentPlaceHolder1_gvResults_ctl01_PageSizeDropDownTop"
PAGE_SIZE_SUBMIT_SEL = "#ctl00_ContentPlaceHolder1_gvResults_ctl01_SelectPageCountTop"

# Real, confirmed second-level status tabs — see module docstring round
# 4. REGISTERED IS FIRST DELIBERATELY — it's the real, confirmed
# default tab that's already loaded from the initial search, so the
# scrape loop skips clicking for whichever tab is first in this list.
# REAL BUG FIX (2026-09-10): an earlier version of this list started
# with Received, which doesn't match reality — Registered is what
# actually loads by default. That mismatch caused Registered's own
# results to be read once (mislabeled "Received"), then read AGAIN via
# a redundant click on its own already-selected tab link, which
# triggered a fresh postback that reset the page size back to its
# default of 10 — confirmed directly from a real run: "Received: 58"
# (really Registered, unclicked) followed by "Registered: 10" (really
# Registered again, reset). The duplicate reference values between
# those two reads of the same real data were also the direct cause of
# a real Postgres "ON CONFLICT DO UPDATE...affect row a second time"
# error on that same run.
STATUS_TABS = [
    ("#ctl00_ContentPlaceHolder1_lbPlanning2ndLevel2", "Registered"),  # real default tab
    ("#ctl00_ContentPlaceHolder1_lbPlanning2ndLevel1", "Received"),
    ("#ctl00_ContentPlaceHolder1_lbPlanning2ndLevel3", "Determined"),
]

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _parse_date_valid(s: str) -> Optional[str]:
    """Real confirmed format from round 4's captured results:
    'Date valid' column shows DD/MM/YYYY (e.g. '04/09/2026')."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_table(html: str, tab_label: str) -> list[dict]:
    """Real, confirmed column order from round 4: Application number,
    Date valid, Site address, Description of proposal — 4 real <td>
    cells per row, reference cell contains the real detail link.
    Falls back to the old generic/defensive regex approach if a row
    doesn't match this shape, with a one-time diagnostic so a genuine
    structure change surfaces rather than silently degrading forever.
    """
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    results_table = soup.find("table", id=re.compile(r"gvResults"))
    tables = [results_table] if results_table else soup.find_all("table")

    for table in tables:
        if table is None:
            continue
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            if len(cells) >= 4:
                ref_cell, date_cell, addr_cell, desc_cell = cells[0], cells[1], cells[2], cells[3]
                reference = ref_cell.get_text(strip=True)
                if not re.match(r"^[A-Z]{2,4}/\d{4}/\d{3,6}$", reference):
                    # Confirmed shape didn't hold for this row — fall
                    # through to the defensive path below rather than
                    # silently saving a wrong reference.
                    pass
                else:
                    link = ref_cell.find("a")
                    detail_url = (urljoin(BASE_URL, link.get("href"))
                                  if link and link.get("href") else None)
                    apps.append({
                        "reference": reference,
                        "submitted_date": _parse_date_valid(date_cell.get_text(strip=True)),
                        "address": addr_cell.get_text(strip=True),
                        "description": desc_cell.get_text(strip=True),
                        "council_url": detail_url,
                        "status_tab": tab_label,
                    })
                    continue

            # Defensive fallback — same regex-based approach as the
            # original scraper, for any row that didn't match the
            # confirmed 4-column shape.
            row_text = " | ".join(c.get_text(strip=True) for c in cells)
            ref_match = re.search(r"\b[A-Z]{2,4}/\d{4}/\d{3,6}\b", row_text)
            if not ref_match:
                continue
            if not _ROW_STRUCTURE_DIAGNOSED:
                _ROW_STRUCTURE_DIAGNOSED = True
                _log(f"⚠ ROW STRUCTURE DIAGNOSTIC ({tab_label}): a row didn't match "
                     f"the confirmed 4-column shape, used defensive fallback. "
                     f"Raw row text: {row_text!r}")
            link = row.find("a")
            detail_url = urljoin(BASE_URL, link.get("href")) if link and link.get("href") else None
            apps.append({
                "reference": ref_match.group(0),
                "description": row_text[:500],
                "council_url": detail_url,
                "status_tab": tab_label,
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


async def _maximise_page_size(page, tab_label: str = "") -> None:
    """Real, confirmed fix from round 4: the page-size dropdown doesn't
    auto-postback on its own — a separate submit button
    (SelectPageCountTop) needs an explicit click. Selecting 100 here
    comfortably covers every real count seen so far (58, 85, 0),
    avoiding "Next"-click pagination entirely. Best-effort — if this
    control isn't present (e.g. a tab with 0 results has nothing to
    resize), that's not an error.

    DIAGNOSTIC ADDED (2026-09-10) — a real run showed Determined
    returning only 10 (the default page size) with NO exception
    logged, despite this same function working correctly for
    Registered moments earlier in the same run. Logging the real
    before/after dropdown value and the real "Showing X of Y" text
    directly, rather than guessing at a third theory blind.
    """
    try:
        size_select = page.locator(PAGE_SIZE_SELECT_SEL)
        count = await size_select.count()
        if count == 0:
            _log(f"  [{tab_label}] Page size dropdown not present on this tab "
                 f"(likely 0 results) — nothing to resize")
            return
        if count > 1:
            _log(f"  [{tab_label}] ⚠ Page size dropdown matched {count} elements "
                 f"(expected 1) — using .first")
            size_select = size_select.first

        before_value = await size_select.input_value()
        _log(f"  [{tab_label}] Page size dropdown value BEFORE resize: {before_value!r}")

        # REAL FIX (2026-09-10, round 2) — confirmed via direct
        # evidence: Determined's dropdown already showed '100' before
        # this function ever touched it (a session-sticky setting
        # carried over from Registered's own earlier resize), so
        # select_option(value="100") was a no-op with no real value
        # change to trigger anything — the real "Showing" text
        # confirmed the grid stayed at its own freshly-loaded default
        # of 10 regardless of what the dropdown claimed. Forcing a
        # genuine transition through "10" first, then "100", guarantees
        # a real change fires either way — whether starting from a
        # true default of 10 or a sticky-but-inactive 100.
        await size_select.select_option(value="10")
        await size_select.select_option(value="100")
        after_select_value = await size_select.input_value()
        _log(f"  [{tab_label}] Page size dropdown value AFTER forced "
             f"10->100 transition (before submit click): {after_select_value!r}")

        submit_btn = page.locator(PAGE_SIZE_SUBMIT_SEL)
        submit_count = await submit_btn.count()
        _log(f"  [{tab_label}] Submit button real count: {submit_count}")
        if submit_count == 0:
            _log(f"  [{tab_label}] ⚠ Submit button not found — cannot resize")
            return

        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit_btn.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        # Real confirmation — what does the "Showing X to Y of Z items"
        # text actually say after the resize attempt completed?
        try:
            body_text = await page.locator("body").inner_text()
            showing_match = re.search(r"Showing \d+ to \d+ of \d+ items", body_text)
            if showing_match:
                _log(f"  [{tab_label}] Real 'Showing' text after resize: "
                     f"{showing_match.group(0)!r}")
        except Exception:
            pass
    except Exception as e:
        _log(f"⚠ Could not maximise page size for {tab_label} (continuing with "
             f"default): {e}")


async def scrape() -> list[dict]:
    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    # REAL FIX (round 3) — the calendar widget's own value comes out
    # dash-formatted, and a plain page.fill() with dashes (no calendar
    # interaction needed) was confirmed sufficient on its own. The old
    # slash format was the actual root cause of every aspxerrorpath
    # redirect seen before this rebuild.
    start_str = start.strftime("%d-%m-%Y")
    end_str = today.strftime("%d-%m-%Y")

    all_apps: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        _log(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
            await page.fill(DATE_FROM_SEL, start_str, timeout=5_000)
            await page.fill(DATE_TO_SEL, end_str, timeout=5_000)
            submit = page.locator(SUBMIT_SEL)
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await submit.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Search fill/submit failed: {e}")
            await context.close()
            await browser.close()
            return []

        if "aspxerrorpath" in page.url:
            _log(f"⚠ Still hit error redirect: {page.url} — real fix may have "
                 f"regressed, stopping here.")
            await context.close()
            await browser.close()
            return []

        _log(f"Post-submit URL: {page.url}")

        # REAL FIX (round 4) — Registered is the default tab, but
        # Determined's real applications are NEVER shown there at all;
        # visiting all 3 confirmed status tabs is required for full
        # coverage, not just parsing whatever loads first.
        for i, (tab_sel, tab_label) in enumerate(STATUS_TABS):
            if i > 0:
                # Registered is already loaded from the initial search
                # (it's the real default) — only Received and
                # Determined need an explicit tab click.
                try:
                    tab_link = page.locator(tab_sel)
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                        await tab_link.click()
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                except Exception as e:
                    _log(f"⚠ Could not switch to {tab_label} tab: {e}")
                    continue

            await _maximise_page_size(page, tab_label)

            html = await page.content()
            tab_apps = _parse_results_table(html, tab_label)
            _log(f"{tab_label}: {len(tab_apps)} real applications parsed")
            if len(tab_apps) >= 100:
                _log(f"⚠ {tab_label} returned {len(tab_apps)} — at or above the "
                     f"100-per-page ceiling this scraper currently assumes. Real "
                     f"pagination beyond page size 100 may be needed if this "
                     f"council's volume grows — not yet built.")
            all_apps.extend(tab_apps)

        await context.close()
        await browser.close()

    # REAL FIX (2026-09-10) — defensive cross-tab deduplication, kept
    # even after fixing the tab-order bug above that was the direct
    # cause of the one real duplicate-reference crash seen so far.
    # Worth keeping regardless: it's plausible for the same real
    # application to genuinely appear in more than one of these tabs
    # (e.g. a status transition happening mid-scrape), and a single
    # duplicate reference in one upsert batch is enough to fail the
    # whole batch with the same Postgres "ON CONFLICT DO UPDATE...
    # affect row a second time" error — same principle already applied
    # in redcar_cleveland_scraper.py after it hit this exact error for
    # a different reason.
    seen_refs: set[str] = set()
    deduped_apps = []
    for a in all_apps:
        if a["reference"] not in seen_refs:
            seen_refs.add(a["reference"])
            deduped_apps.append(a)
    if len(deduped_apps) != len(all_apps):
        _log(f"Cross-tab dedup: {len(all_apps)} -> {len(deduped_apps)} "
             f"(removed {len(all_apps) - len(deduped_apps)} duplicate reference(s) "
             f"seen in more than one tab)")

    return deduped_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Telford and Wrekin scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"telford_councils.py. Run the INSERT_SQL there, look up "
              f"the real id, and fill it in before running this scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results parsed. Nothing to save.")
        return

    records = []
    for a in raw_apps:
        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "address": a.get("address"),
            "description": a.get("description", "")[:500] or None,
            "submitted_date": a.get("submitted_date"),
            # HONEST LIMITATION — see module docstring: the list view
            # doesn't expose the real decision outcome for Determined
            # applications, only that one exists. Everything files as
            # 'pending' for now regardless of which real tab it came
            # from.
            "status": "pending",
            "council_url": a.get("council_url"),
            "source": "telford_scraper",
        })

    _log(f"Upserting {len(records)} records with council_id={cid}")
    ok = await _supa_upsert(records)
    if ok:
        _log(f"✓ Saved {len(records)}")
        await _supa_patch_council(cid, {
            "coverage_source": "telford_bespoke",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
