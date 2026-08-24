#!/usr/bin/env python3
"""
PlanFind — South Tyneside Council scraper (2026-08-19).

Genuinely different technology from the other 3 Northgate councils
(see northgate_servlet_scraper.py) — real ASP.NET WebForms
(__VIEWSTATE/__EVENTVALIDATION postback machinery), not a simple
servlet form. Real, confirmed evidence from
northgate_servlet_family_recon.py, gathered across THREE separate
runs:

  - Real search page: NewApplicationsSearch.aspx. A cookie/consent
    overlay (#ivcb-overlay) blocks all interaction until dismissed.
  - Real date control: NOT free-text fields — a "vrDays" dropdown
    (1-31, "goes back N days"), confirmed matching the council's own
    documented "goes back 31 days" behaviour.
  - CONFIRMED, with real evidence across 3 separate runs, 3 different
    session tokens every time: results land at a DYNAMICALLY
    GENERATED, one-time URL —
    .../Generic/StdResults.aspx?...XMLLoc=/Northgate/PlanningExplorer/
    Generic/XMLtemp/<session-token>/<guid>.xml — genuinely different
    every single search, cannot be hardcoded or reused across runs.
    The scraper MUST drive a real, live search every time it runs, no
    shortcuts.
  - REAL, IMPORTANT NUANCE also confirmed directly: that dynamic URL
    IS stable WITHIN one search session for pagination purposes — all
    of a single search's real "page 2/3/4" links reuse the identical
    XMLLoc token/filename, only a "p=" offset parameter changes (p=10,
    20, 30... matching PS=10 page size). So the dynamic URL only needs
    capturing ONCE per run (right after the search submits), then
    every subsequent page is a direct, cheap URL navigation with a
    modified "p=" value — no repeated clicking through pagination
    links needed.
  - Real results table: 6 columns — Application Number | Site Address
    | Development Description | Status | Date Registered | Decision.
    "Status" is a real WORKFLOW STAGE (e.g. "REGISTERED"), NOT a
    decision outcome — deliberately not used for this project's own
    `status` field, same discipline as every other scraper here. Only
    the real, separate "Decision" column (empty until resolved) is
    used for that.
  - Real detail-page link contains a real, stable PARAM0 numeric id —
    confirmed reusable for a pending-recheck detail-page revisit,
    same purpose as this project's other platforms' recheck mechanisms.

HONEST LIMITATIONS:
  - MAX_PAGES below caps how many pages of a large result set get
    fetched per run — a real, deliberate safety net against runaway
    time on a search with an unusually large number of results,
    matching the same discipline as Idox's own page-count caps
    elsewhere in this project. Not confirmed to ever actually trigger
    in practice — South Tyneside's real 31-day window showed 40
    results (4 pages) in the recon that discovered this platform.
  - The real vrDays dropdown's exact available option values were
    confirmed as "1" through "31" (32 total options including
    whatever the first, unlabelled option represents) — selecting by
    the literal label "31" (the widest real range) is what recon
    confirmed working directly.
"""
import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeout

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

BASE_URL = "https://planning.southtyneside.info"
SEARCH_URL = f"{BASE_URL}/Northgate/PlanningExplorer/NewApplicationsSearch.aspx"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = os.environ.get("DAYS_BACK", "31")  # real vrDays option label,
                                                     # matching the confirmed
                                                     # "goes back 31 days"
                                                     # real evidence
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "20"))  # real safety net —
                                                          # see module docstring
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "50"))

# The real, actual DB council_id for South Tyneside — needs a real row
# created first via the INSERT_SQL below, same pattern as every other
# new council this session.
COUNCIL_DB_ID: Optional[int] = 484

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('South Tyneside Council','south-tyneside-council','northgate_south_tyneside','england','https://planning.southtyneside.info/Northgate/PlanningExplorer/NewApplicationsSearch.aspx','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'northgate_south_tyneside',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
    """Applied ONLY to the real, separate 'Decision' column — never to
    'Status' (a real workflow stage, e.g. 'REGISTERED', not an
    outcome), same discipline as every other scraper in this project."""
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


def _parse_uk_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_param0(url: str) -> Optional[str]:
    try:
        qs = parse_qs(urlparse(url).query)
        vals = qs.get("PARAM0")
        return vals[0] if vals else None
    except Exception:
        return None


_ROW_PARSE_DIAGNOSED = False


def _diagnose_row_parse(headers: list[str], html_snippet: str):
    global _ROW_PARSE_DIAGNOSED
    if _ROW_PARSE_DIAGNOSED:
        return
    _ROW_PARSE_DIAGNOSED = True
    print(f"    [South Tyneside Council] ROW PARSE DIAGNOSTIC: real headers "
          f"found: {headers!r}. Response snippet: {html_snippet[:500]!r}")


def _parse_results_table(html: str) -> list[dict]:
    """Real, confirmed table structure: 6 columns — Application Number
    | Site Address | Development Description | Status | Date
    Registered | Decision. Matched by real header text, not fixed
    position, same defensive convention as every other scraper here."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        _diagnose_row_parse([], html)
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        _diagnose_row_parse([], html)
        return []

    header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all("th")]
    if not header_cells:
        _diagnose_row_parse([], html)
        return []

    def _col_index(*keywords) -> Optional[int]:
        for i, h in enumerate(header_cells):
            if any(kw in h for kw in keywords):
                return i
        return None

    idx_ref = _col_index("application number")
    idx_address = _col_index("site address", "address")
    idx_proposal = _col_index("development description", "proposal", "description")
    idx_date = _col_index("date registered", "date")
    idx_decision = _col_index("decision")

    if idx_ref is None or idx_address is None:
        _diagnose_row_parse(header_cells, html)
        return []

    apps = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= max(idx_ref, idx_address):
            continue

        ref_cell = cells[idx_ref]
        link = ref_cell.find("a")
        # REAL FIX: the link contains a hidden accessibility span
        # ("View more details for ") immediately before the real
        # reference text — confirmed directly from the actual captured
        # markup. get_text() on the whole cell/link grabs both,
        # producing "View more details for260500" instead of "260500".
        # Removing the known real span text specifically rather than
        # just taking the last text node, since that's more robust to
        # any other real markup variation.
        raw_ref_text = ref_cell.get_text(strip=True)
        reference = re.sub(r"view more details for\s*", "", raw_ref_text, flags=re.I).strip()
        detail_url = urljoin(BASE_URL + "/Northgate/PlanningExplorer/", link["href"]) if link and link.get("href") else None
        param0 = _extract_param0(detail_url) if detail_url else None

        address = cells[idx_address].get_text(" ", strip=True) if idx_address < len(cells) else ""
        address = re.sub(r"\s*,\s*", ", ", address).strip()
        postcode = _extract_postcode(address)

        proposal = cells[idx_proposal].get_text(strip=True) if idx_proposal is not None and idx_proposal < len(cells) else ""

        registered_date = None
        if idx_date is not None and idx_date < len(cells):
            registered_date = _parse_uk_date(cells[idx_date].get_text(strip=True))

        decision_text = ""
        if idx_decision is not None and idx_decision < len(cells):
            decision_text = cells[idx_decision].get_text(strip=True)

        if not reference:
            continue

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": registered_date,
            "status": _normalise_status(decision_text),
            "council_url": detail_url,
            "param0": param0,
        })

    if not apps:
        _diagnose_row_parse(header_cells, html)

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


def _set_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    qs[key] = [value]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    print(f"    [South Tyneside Council] {msg}")


async def _dismiss_overlay(page):
    for selector in ["#ivcb-overlay button", "#ivcb-overlay .accept",
                      "button:has-text('Accept')", "button:has-text('I agree')",
                      "button:has-text('Close')", "[id*='cookie'] button"]:
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible(timeout=2000):
                await el.click(timeout=3000)
                await asyncio.sleep(1)
                return
        except Exception:
            continue


async def scrape(browser: Browser) -> list[dict]:
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        _log(f"⚠ Could not load search page: {e}")
        await context.close()
        return []

    await _dismiss_overlay(page)

    try:
        sel = page.locator("select[name='vrDays']")
        await sel.select_option(label=DAYS_BACK)
    except Exception as e:
        _log(f"⚠ Could not select {DAYS_BACK!r} in vrDays dropdown: {e}")

    try:
        btn = page.locator("input[name='csbtnSearch']")
        await btn.click(timeout=10_000)
    except Exception as e:
        _log(f"⚠ Could not click search button: {e}")
        await context.close()
        return []

    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)

    # REAL, confirmed dynamic URL, captured ONCE here — stable for
    # pagination purposes within this one search (see module docstring)
    base_results_url = page.url
    if "StdResults.aspx" not in base_results_url:
        _log(f"⚠ Did not land on a real results page — got: {base_results_url}")
        await context.close()
        return []

    html = await page.content()
    all_apps = _parse_results_table(html)

    # Real total-record-count check, to know how many more pages exist
    text = await page.locator("body").inner_text()
    m = re.search(r"records\s+\d+\s+to\s+\d+\s+of\s+(\d+)", text, re.I)
    total_records = int(m.group(1)) if m else len(all_apps)
    _log(f"{len(all_apps)} results on page 1 (Records ... of {total_records} total)")

    page_size = 10  # real, confirmed PS=10 in every captured URL
    total_pages = min((total_records + page_size - 1) // page_size, MAX_PAGES)

    for page_num in range(2, total_pages + 1):
        if should_stop():
            _log(f"⚠ Time budget reached, stopping at page {page_num - 1} of {total_pages}")
            break
        offset = (page_num - 1) * page_size
        page_url = _set_query_param(base_results_url, "p", str(offset))
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Could not load page {page_num}: {e}")
            continue

        page_html = await page.content()
        page_apps = _parse_results_table(page_html)
        _log(f"{len(page_apps)} results on page {page_num}")
        all_apps.extend(page_apps)

    if total_pages >= MAX_PAGES:
        _log(f"⚠ MAX_PAGES cap ({MAX_PAGES}) reached — {total_records} total "
             f"records exist, only the first {MAX_PAGES * page_size} were fetched")

    await context.close()
    return all_apps


async def recheck_pending(browser: Browser, pending: list[dict]) -> list[dict]:
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

        text = await page.locator("body").inner_text()
        m = re.search(r"decision\s*:?\s*\n?\s*([A-Za-z ,.'-]+)", text, re.I)
        if m:
            decision_text = m.group(1).strip()
            status = _normalise_status(decision_text)
            if status != "pending":
                updates.append({"reference": p["reference"], "status": status})
    await context.close()
    if updates:
        _log(f"Recheck: {len(updates)} of {len(pending)} previously-pending "
             f"application(s) now have a real decision")
    return updates


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind South Tyneside scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"Max pages:   {MAX_PAGES}")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    if COUNCIL_DB_ID is None:
        print("ERROR: COUNCIL_DB_ID is still None. Run this file's INSERT_SQL "
              "in Supabase first, then set COUNCIL_DB_ID to the real id "
              "Supabase assigns.")
        sys.exit(1)

    pending = []
    try:
        pending = await _supa_get(
            "planning_applications",
            council_id=f"eq.{COUNCIL_DB_ID}",
            status="eq.pending",
            select="reference,council_url",
            limit=str(RECHECK_LIMIT),
        )
        print(f"Pending recheck: {len(pending)} applications (bounded to {RECHECK_LIMIT})\n")
    except Exception as e:
        print(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        raw_apps = await scrape(browser)
        recheck_updates = await recheck_pending(browser, pending)

        await browser.close()

    # REAL FIX — same bug already found and fixed in esl_scraper.py:
    # this branch was unconditionally resetting coverage_source back
    # to 'pending' on ANY run that found zero new applications — even
    # when caused by a genuine, transient scraping error, silently
    # downgrading a council that may have real, valid data from an
    # earlier successful run. coverage_source should only ever be SET
    # on a real success, never reset to 'pending' just because one
    # particular run happened to find nothing.
    if not raw_apps and not recheck_updates:
        print("\nNo results and no recheck updates — nothing to save.")
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
            "council_id": COUNCIL_DB_ID,
            "reference": a["reference"],
            "address": a["address"] or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "submitted_date": a.get("submitted_date"),
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "northgate_south_tyneside_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    for u in recheck_updates:
        records.append({
            "council_id": COUNCIL_DB_ID,
            "reference": u["reference"],
            "status": u["status"],
        })

    if records:
        _log(f"Upserting {len(records)} records with council_id={COUNCIL_DB_ID}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(COUNCIL_DB_ID, {
                "coverage_source": "northgate_south_tyneside",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
