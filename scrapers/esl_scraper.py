#!/usr/bin/env python3
"""
PlanFind — "Search/Advanced" platform family scraper (2026-08-22,
extended 2026-08-23).

4 councils on one shared platform: Westmorland and Furness Council
(Eden/South Lakeland areas only — Barrow is separate), Cherwell,
Wychavon, Malvern Hills — see esl_councils.py for the full, real,
confirmed evidence backing every design decision here, gathered
across 6 recon rounds for Eden/South Lakeland plus a focused
confirmation pass for the other 3.

ARCHITECTURE: check the real "Planning" search-type checkbox, fill the
real date-range fields, submit, parse the real results table
(stripping the real hidden accessibility-label spans first), then
repeatedly click the real "Next" link — confirmed via direct network
capture that only a genuine click (not a manually reconstructed URL)
correctly triggers the site's own jQuery Unobtrusive AJAX pagination.

HONEST LIMITATIONS:
  - No decision/status info exists in the search results list at all
    — every application starts 'pending'. A pending-recheck pass
    against the real, stable /Planning/Display/{reference} URL is the
    only route to a real decision, same as Hartlepool's situation in
    the Northgate servlet family. The real detail-page field labels
    for decision info have never actually been recon'd — the recheck
    logic below uses a defensive keyword search, not a confirmed
    label, same discipline used before a detail page has ever been
    directly seen elsewhere in this project.
  - North Warwickshire Borough Council deliberately NOT included —
    confirmed to redirect through a real disclaimer-acceptance page
    first, a genuinely different flow needing its own dedicated
    handling before it can be added safely.
  - The real "Planning" checkbox (#SearchPlanning) is checked
    unconditionally for every council here, even though Eden/South
    Lakeland's own search worked without it — confirmed harmless, and
    Cherwell/Wychavon/Malvern Hills all confirmed to silently reject
    an unchecked submission (just re-serving a blank form), so
    checking it universally is the safer, more robust choice.
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

from esl_councils import ESL_COUNCILS, COUNCIL_DB_IDS

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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "1"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "20"))  # real safety cap —
                                                          # 20 pages x 10 =
                                                          # 200, comfortably
                                                          # above the 114
                                                          # real total seen
                                                          # in recon
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "50"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
    """Applied ONLY to real decision text found on a detail page during
    a recheck pass — NEVER to the search results list's own 'Status'
    column, which is a real, confirmed workflow stage (e.g. 'Valid',
    'Consultation Started'), not a decision outcome."""
    if not s:
        return "pending"
    s = s.lower()
    if any(x in s for x in ("approv", "grant", "permit", "allow", "no objection")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
    return "pending"


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_cell_text(cell) -> str:
    """Real, confirmed fix: every cell contains a real, hidden
    accessibility-label span (class='mobile-heading', e.g.
    'Application No.', 'Location') BEFORE the real content. Extracting
    it first so it never bleeds into the real value — same discipline
    as South Tyneside's 'View more details for' fix earlier."""
    span = cell.find("span", class_="mobile-heading")
    if span:
        span.extract()
    text = cell.get_text("\n", strip=True)
    parts = [" ".join(p.split()) for p in text.split("\n") if p.strip()]
    return ", ".join(parts)


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


_ROW_PARSE_DIAGNOSED: set[str] = set()


def _diagnose_row_parse(council_name: str, headers: list[str], html_snippet: str):
    if council_name in _ROW_PARSE_DIAGNOSED:
        return
    _ROW_PARSE_DIAGNOSED.add(council_name)
    print(f"    [{council_name}] ROW PARSE DIAGNOSTIC: real "
          f"headers found: {headers!r}. Response snippet: {html_snippet[:500]!r}")


def _parse_results_table(html: str, base_url: str, council_name: str) -> list[dict]:
    """Real, confirmed table structure: single <table>. Header row uses
    plain <td> for Eden/South Lakeland, Wychavon, and Malvern Hills
    (confirmed identical: Application Number | Location | Proposal |
    Status) — but Cherwell's real header row uses <th> instead,
    confirmed directly (an empty header was returned when only <td>
    was checked, despite Cherwell genuinely having real result rows).
    Checking both. Real accessibility-label spans stripped from every
    cell before extraction (see _clean_cell_text)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        _diagnose_row_parse(council_name, [], html)
        return []

    rows = table.find_all("tr")
    if len(rows) < 1:
        _diagnose_row_parse(council_name, [], html)
        return []

    header_row_cells = rows[0].find_all("td") or rows[0].find_all("th")
    # REAL FIX, confirmed via Cherwell's production run: sortable
    # column headers there have real, decorative sort-arrow unicode
    # characters (▶/▲) glued directly onto the header text (e.g.
    # "reference no.▶") — stripping these so keyword matching below
    # isn't affected by decoration that has nothing to do with the
    # real column name.
    header_cells = [
        c.get_text(strip=True).lower().rstrip("▶▲▼◀")
        for c in header_row_cells
    ]
    if not header_cells:
        _diagnose_row_parse(council_name, [], html)
        return []

    def _col_index(*keywords) -> Optional[int]:
        for i, h in enumerate(header_cells):
            if any(kw in h for kw in keywords):
                return i
        return None

    # REAL FIX, confirmed via Cherwell's production run: its real
    # header says "reference no." — genuinely different wording from
    # Eden/South Lakeland/Wychavon/Malvern Hills' "application number".
    # Matching both.
    idx_ref = _col_index("application number", "reference no")
    idx_location = _col_index("location")
    idx_proposal = _col_index("proposal")
    idx_status = _col_index("status")

    if idx_ref is None or idx_location is None:
        _diagnose_row_parse(council_name, header_cells, html)
        return []

    apps = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= max(idx_ref, idx_location):
            continue

        ref_cell = cells[idx_ref]
        link = ref_cell.find("a")
        reference = _clean_cell_text(ref_cell)
        detail_url = None
        if link and link.get("href"):
            href = link["href"]
            detail_url = href if href.startswith("http") else f"{base_url}{href}"

        if not reference:
            continue

        address = _clean_cell_text(cells[idx_location]) if idx_location < len(cells) else ""
        postcode = _extract_postcode(address)
        proposal = _clean_cell_text(cells[idx_proposal]) if idx_proposal is not None and idx_proposal < len(cells) else ""
        status_text = _clean_cell_text(cells[idx_status]) if idx_status is not None and idx_status < len(cells) else ""

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "status_workflow_stage": status_text,  # real workflow stage,
                                                       # NOT our own status
            "status": "pending",  # always starts pending — see module
                                     # docstring's honest limitation
            "council_url": detail_url,
        })

    if not apps:
        _diagnose_row_parse(council_name, header_cells, html)

    return apps


def _parse_card_results(html: str, base_url: str) -> list[dict]:
    """Real, confirmed alternate results structure — North Warwickshire's
    newer front-end template renders results as styled cards
    (div.searchResultsCardRow) rather than a <table>, confirmed
    directly from real captured HTML:
      <div class="row searchResultsCardRow">
        <div class="col-xs-12"><a href="/Planning/Display?applicationNumber=REF">ADDRESS</a></div>
        <div class="col-xs-12"><h2>REFERENCE</h2><span>TYPE</span><span>DECISION?</span></div>
        <div class="col-xs-12 col-md-12">DESCRIPTION</div>
      </div>
    Real, permanent detail URL confirmed: /Planning/Display?
    applicationNumber={reference} (URL-encoded)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_="searchResultsCardRow")

    apps = []
    for card in cards:
        cols = card.find_all("div", recursive=False)
        if len(cols) < 2:
            continue

        addr_link = cols[0].find("a")
        # Real, confirmed format: embedded literal newlines within the
        # link's own text (not <br/> tags) — e.g. "Rear of block 20-26
        # Church Hill \nColeshill\nB46 3AJ". Splitting and rejoining
        # with commas, same real convention as every other address
        # field in this project.
        raw_address = addr_link.get_text("\n", strip=True) if addr_link else ""
        address = ", ".join(p.strip() for p in raw_address.split("\n") if p.strip())
        detail_url = None
        if addr_link and addr_link.get("href"):
            href = addr_link["href"]
            detail_url = href if href.startswith("http") else f"{base_url}{href}"

        h2 = cols[1].find("h2")
        reference = h2.get_text(strip=True) if h2 else ""
        spans = cols[1].find_all("span")
        # Real, confirmed: first span is a type/category label, second
        # (often empty) is presumably a real decision value once one
        # exists — never used for our own status, same discipline as
        # every other platform here.
        decision_span_text = spans[1].get_text(strip=True) if len(spans) > 1 else ""

        proposal = ""
        if len(cols) > 2:
            # Real, confirmed minor formatting quirks: embedded literal
            # newlines and non-breaking spaces (\xa0) both appear in
            # real description text — collapsing all whitespace
            # uniformly via split()/join(), same approach used
            # elsewhere in this project rather than a simple get_text
            # separator alone.
            proposal = " ".join(cols[2].get_text(" ", strip=True).split())

        if not reference:
            continue

        postcode = _extract_postcode(address)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "status_workflow_stage": decision_span_text,
            "status": _normalise_status(decision_span_text) if decision_span_text else "pending",
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


# ---------------------------------------------------------------------------
def _log(council_name: str, msg: str) -> None:
    print(f"    [{council_name}] {msg}")


async def scrape(browser: Browser, council_name: str, base_url: str, days_back: int) -> list[dict]:
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(f"{base_url}/Search/Advanced", wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        _log(council_name, f"⚠ Could not load search page: {e}")
        await context.close()
        return []

    # ADDED 2026-08-23 — real, confirmed via nwarks_disclaimer_recon.py:
    # North Warwickshire redirects to a real disclaimer-acceptance page
    # before /Search/Advanced becomes accessible. A plain "Accept"
    # button, no checkbox involved. Checking the real URL for this
    # rather than assuming — every other council in this family never
    # shows this gate at all, so this only fires when genuinely needed.
    if "/Disclaimer" in page.url:
        try:
            accept_btn = page.get_by_role("button", name="Accept", exact=True)
            await accept_btn.first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1)
            _log(council_name, f"Accepted a real disclaimer gate")
        except Exception as e:
            _log(council_name, f"⚠ Could not accept disclaimer: {e}")
            await context.close()
            return []

    today = date.today()
    start = today - timedelta(days=days_back)

    try:
        # REAL FIX — confirmed via direct error inspection on North
        # Warwickshire: its #DateReceivedFrom/To are genuine HTML5
        # type="date" inputs (every other council uses plain
        # type="text"), hidden behind a JS date-picker library that
        # blocks Playwright's normal fill(). Setting the value
        # directly via JS in ISO format (the format native date inputs
        # actually require) and dispatching real input/change events,
        # same category of fix as Northgate's readonly fields earlier
        # this project. Checking the real type attribute first so this
        # doesn't change behaviour for the other 4 councils at all.
        # REAL FIX — confirmed via Cherwell's production run: its page
        # genuinely has 2 real elements sharing id="DateReceivedFrom"
        # (a real, pre-existing HTML quality issue on their end,
        # confirmed via the exact real error showing both elements'
        # slightly different attributes). Playwright's fill() tolerated
        # this silently before; get_attribute() is stricter. Using
        # .first, same convention as every other locator interaction
        # in this file.
        field_type = await page.locator("#DateReceivedFrom").first.get_attribute("type")
        if field_type == "date":
            for field_id, value in [("DateReceivedFrom", start.isoformat()),
                                      ("DateReceivedTo", today.isoformat())]:
                await page.evaluate(
                    """([id, val]) => {
                        const el = document.getElementById(id);
                        el.value = val;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }""",
                    [field_id, value],
                )
        else:
            await page.locator("#DateReceivedFrom").first.fill(start.strftime("%d/%m/%Y"), timeout=5_000)
            await page.locator("#DateReceivedTo").first.fill(today.strftime("%d/%m/%Y"), timeout=5_000)
    except Exception as e:
        _log(council_name, f"⚠ Could not fill date fields: {e}")
        await context.close()
        return []

    # ADDED 2026-08-23 — real, confirmed via direct screenshot evidence:
    # Cherwell/Wychavon/Malvern Hills all silently reject a search with
    # no top-level "Planning" checkbox ticked, just re-serving a blank
    # form. Eden/South Lakeland's own search worked without this, but
    # checking it unconditionally is harmless and makes the whole
    # family more robust.
    try:
        planning_checkbox = page.locator("#SearchPlanning")
        if await planning_checkbox.count() > 0:
            await planning_checkbox.first.check(timeout=3_000)
    except Exception as e:
        _log(council_name, f"⚠ Could not check #SearchPlanning (continuing anyway): {e}")

    try:
        # REAL FIX — confirmed via direct testing that a generic
        # "button:has-text('Search')" selector can hijack the wrong
        # element (Cherwell has an unrelated site-header search toggle
        # containing the same text). Scoping the click specifically to
        # a button inside the real form that contains the date fields
        # just filled, same fix already proven in
        # search_advanced_family_recon.py.
        form_with_dates = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
        search_btn = form_with_dates.locator("button:has-text('Search')")
        if await search_btn.count() == 0:
            search_btn = form_with_dates.locator("input[type='submit']")
        await search_btn.first.click(timeout=5_000)
    except Exception as e:
        _log(council_name, f"⚠ Could not click search button: {e}")
        await context.close()
        return []

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)

    all_apps: list[dict] = []
    seen_refs: set[str] = set()
    page_num = 1

    while page_num <= MAX_PAGES:
        if should_stop():
            _log(council_name, f"⚠ Time budget reached, stopping at page {page_num}")
            break

        html = await page.content()
        page_apps = _parse_results_table(html, base_url, council_name)
        # REAL FIX — confirmed via North Warwickshire: its newer
        # front-end renders results as styled cards, not a <table> at
        # all. Falling back to the card parser only when the table
        # parser genuinely finds nothing, so this never changes
        # behaviour for the other 4 councils.
        if not page_apps:
            page_apps = _parse_card_results(html, base_url)

        new_count = 0
        for a in page_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
                new_count += 1

        # Real, confirmed total count text, e.g. "(114)" — used as a
        # real early-exit signal so pagination doesn't run longer than
        # genuinely needed
        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        total_match = re.search(r"\((\d+)\)", body_text)
        real_total = int(total_match.group(1)) if total_match else None

        _log(council_name, f"Page {page_num}: {new_count} new (running total {len(all_apps)}"
             + (f" of {real_total} real total" if real_total else "") + ")")

        if real_total is not None and len(all_apps) >= real_total:
            break

        # REAL, CONFIRMED PAGINATION (6 recon rounds to establish this):
        # a genuine click on the live "Next" element, letting its own
        # real client-side JS handler make the correctly-authenticated
        # AJAX call — manually reconstructing the target URL does NOT
        # work (confirmed: 200-but-empty via plain navigation, 404 via
        # a manually replicated AJAX header).
        try:
            next_link = page.get_by_text("Next", exact=True)
            if await next_link.count() > 0:
                await next_link.first.click(timeout=5_000)
            else:
                # REAL FIX — confirmed via North Warwickshire: its
                # newer front-end uses an icon-based chevron-right
                # pagination control instead of plain "Next" text, but
                # the SAME real data-ajax-target mechanism underneath.
                # Its real parent <li> gets a "disabled" class on the
                # last page, used here as the real stopping condition.
                chevron_li = page.locator("li:has(i.fa-chevron-right)")
                if await chevron_li.count() == 0:
                    break
                classes = await chevron_li.first.get_attribute("class") or ""
                if "disabled" in classes:
                    break
                await chevron_li.first.locator("a").first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1.5)
            page_num += 1
        except Exception as e:
            _log(council_name, f"⚠ Could not click Next/chevron (page {page_num}): {e}")
            break

    await context.close()
    return all_apps


async def recheck_pending(browser: Browser, council_name: str, pending: list[dict]) -> list[dict]:
    """Real, confirmed permanent detail URL (/Planning/Display/{ref}) —
    same purpose as this project's other pending-recheck passes. Real
    detail-page field labels never actually recon'd — using a
    defensive keyword search, same discipline as before a detail page
    has ever been directly seen elsewhere in this project."""
    if not pending:
        return []
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    updates = []
    for p in pending:
        if should_stop():
            _log(council_name, f"⚠ Time budget reached mid-recheck, stopping")
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
            pass
        m = re.search(r"decision\s*:?\s*\n?\s*([A-Za-z ,.'-]+)", text, re.I)
        if m:
            decision_text = m.group(1).strip()
            status = _normalise_status(decision_text)
            if status != "pending":
                updates.append({"reference": p["reference"], "status": status})
    await context.close()
    if updates:
        _log(council_name, f"Recheck: {len(updates)} of {len(pending)} previously-pending "
             f"application(s) now have a real decision")
    return updates


async def process_council(name: str, base_url: str, browser: Browser, sem: asyncio.Semaphore):
    async with sem:
        cid = COUNCIL_DB_IDS[name]
        print(f"\n[{name}] (council_id={cid})")

        if should_stop():
            print(f"    [{name}] — skipping, time budget reached "
                  f"({elapsed_minutes():.1f} min elapsed)")
            return

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
                _log(name, f"Pending recheck: {len(pending)} applications "
                     f"(bounded to {RECHECK_LIMIT})")
        except Exception as e:
            _log(name, f"⚠ Failed to fetch pending recheck list (continuing "
                 f"without it): {e}")

        try:
            raw_apps = await scrape(browser, name, base_url, DAYS_BACK)
        except Exception as e:
            print(f"    [{name}] ✗ Error: {e}")
            return

        try:
            recheck_updates = await recheck_pending(browser, name, pending)
        except Exception as e:
            _log(name, f"⚠ Recheck error: {e}")
            recheck_updates = []

        if not raw_apps and not recheck_updates:
            await _supa_patch_council(cid, {"coverage_source": "pending"})
            return

        postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
        coords = await geocode(postcodes) if postcodes else {}
        if postcodes:
            _log(name, f"Geocoding {len(postcodes)} postcodes…")

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
                "address": a["address"] or None,
                "postcode": a.get("postcode"),
                "description": a.get("description") or None,
                "status": a["status"],
                "council_url": a.get("council_url"),
                "lat": lat,
                "lng": lng,
                "source": "esl_scraper",
            })

        if fallback_count:
            _log(name, f"Council centroid fallback for {fallback_count} apps")

        # REAL FIX — confirmed via Wychavon/Malvern Hills' production
        # run: PostgREST's bulk upsert genuinely requires every object
        # in a SINGLE call to share identical keys (real error:
        # "All object keys must match"). Full application records (10
        # keys) and partial recheck-update records (3 keys) were being
        # mixed into the same call — this bug was latent since the
        # very first version, it just never triggered until a run
        # found real recheck updates for the first time. Two separate
        # calls now, each internally uniform.
        recheck_records = [{
            "council_id": cid,
            "reference": u["reference"],
            "status": u["status"],
        } for u in recheck_updates]

        saved_count = 0
        if records:
            _log(name, f"Upserting {len(records)} new/updated application records "
                 f"with council_id={cid}")
            if await _supa_upsert(records):
                saved_count += len(records)

        if recheck_records:
            _log(name, f"Upserting {len(recheck_records)} recheck status updates "
                 f"with council_id={cid}")
            if await _supa_upsert(recheck_records):
                saved_count += len(recheck_records)

        if saved_count:
            _log(name, f"✓ Saved {saved_count}")
            await _supa_patch_council(cid, {
                "coverage_source": "esl_advanced_search",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind 'Search/Advanced' family scraper")
    print(f"Councils:    {len(ESL_COUNCILS)}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"Max pages:   {MAX_PAGES}")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder (None) DB id "
              "in esl_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun esl_councils.py's INSERT_SQL in Supabase first, then replace "
              "each None above with the real id Supabase assigns.")
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        sem = asyncio.Semaphore(CONCURRENCY)
        await asyncio.gather(*[
            process_council(name, base_url, browser, sem)
            for name, base_url in ESL_COUNCILS
        ])

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
