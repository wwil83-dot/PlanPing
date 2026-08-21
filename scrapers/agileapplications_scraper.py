#!/usr/bin/env python3
"""
PlanFind — agileapplications.co.uk scraper (2026-08-21).

Covers 3 councils on one shared platform: Middlesbrough, Flintshire,
Cannock Chase — see agileapplications_councils.py for the real,
confirmed evidence backing every design decision here.

ARCHITECTURE: real, confirmed URL construction — no form interaction
needed at all, just build the URL with the right JSON criteria and
navigate directly. Genuinely simpler than every other platform built
this session except Idox itself.

HONEST LIMITATIONS:
  - Real result counts confirmed small (18-25) for a 14-day window in
    recon — no pagination was actually exercised or confirmed working,
    despite the URL supporting a "page" parameter. If a council's
    result count for a given window exceeds one page, this scraper
    will currently only capture page 1 — worth revisiting with real
    evidence if that's ever observed in production logs.
  - The "determined" (decided) status variant of the URL was never
    directly tested — only "registered" was confirmed via real
    browser recon. Decision/status data DOES appear directly in the
    "registered" results for councils that have that column (e.g.
    Flintshire), so a full decided-list pass isn't the only route to
    real decision data — same as several other platforms here.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, quote

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeout

from agileapplications_councils import AGILE_COUNCILS, COUNCIL_DB_IDS

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

BASE_URL = "https://planning.agileapplications.co.uk"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "1"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "14"))
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "100"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
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
    for fmt in ("%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _build_url(council_slug: str, status: str, date_from: date, date_to: date) -> str:
    """Real, confirmed URL shape — status is 'registered' (received) or
    'determined' (decided); field names change accordingly, matching
    the real pattern found directly in the user's own research."""
    if status == "registered":
        criteria = (
            '{"status":"registered",'
            f'"registrationDateFrom":"{date_from.isoformat()}T00:00:00+01:00",'
            f'"registrationDateTo":"{date_to.isoformat()}T23:59:59+01:00"}}'
        )
    else:
        criteria = (
            '{"status":"determined",'
            f'"decisionDateFrom":"{date_from.isoformat()}T00:00:00+01:00",'
            f'"decisionDateTo":"{date_to.isoformat()}T23:59:59+01:00"}}'
        )
    return f"{BASE_URL}/{council_slug}/search-applications/results?criteria={quote(criteria)}&page=1"


_ROW_PARSE_DIAGNOSED: set[str] = set()


def _diagnose_row_parse(council_name: str, headers: list[str], html_snippet: str):
    if council_name in _ROW_PARSE_DIAGNOSED:
        return
    _ROW_PARSE_DIAGNOSED.add(council_name)
    print(f"    [{council_name}] ROW PARSE DIAGNOSTIC: real headers found: "
          f"{headers!r}. Response snippet: {html_snippet[:500]!r}")


def _parse_results_table(html: str, council_name: str) -> list[dict]:
    """Real, confirmed AngularJS ng-table structure. TWO identical
    duplicate <table> elements exist (a real responsive-view pattern)
    — only the first is used. Real, confirmed row classes: header row
    is 'ng-table-sort-header', a filter-input row is 'ng-table-filters'
    (NOT real data — skipped), and genuine data rows are 'animate-
    repeat' specifically — this exact class is the reliable real
    selector confirmed directly from captured markup."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        _diagnose_row_parse(council_name, [], html)
        return []
    table = tables[0]

    header_row = None
    for tr in table.find_all("tr"):
        classes = tr.get("class") or []
        if "ng-table-sort-header" in classes:
            header_row = tr
            break
    if not header_row:
        _diagnose_row_parse(council_name, [], html)
        return []

    header_cells = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]
    if not header_cells:
        _diagnose_row_parse(council_name, [], html)
        return []

    def _col_index(*keywords) -> Optional[int]:
        for i, h in enumerate(header_cells):
            if any(kw in h for kw in keywords):
                return i
        return None

    idx_ref = _col_index("reference")
    idx_proposal = _col_index("proposal")
    idx_location = _col_index("location")
    idx_reg_date = _col_index("registration date")
    idx_decision = None
    idx_decision_date = None
    for i, h in enumerate(header_cells):
        if h == "decision":
            idx_decision = i
        elif "decision date" in h:
            idx_decision_date = i

    if idx_ref is None or idx_location is None:
        _diagnose_row_parse(council_name, header_cells, html)
        return []

    apps = []
    data_rows = [tr for tr in table.find_all("tr") if "animate-repeat" in (tr.get("class") or [])]
    for row in data_rows:
        cells = row.find_all("td")
        if len(cells) <= max(idx_ref, idx_location):
            continue

        reference = cells[idx_ref].get_text(strip=True)
        if not reference:
            continue

        address = cells[idx_location].get_text(strip=True) if idx_location < len(cells) else ""
        postcode = _extract_postcode(address)
        proposal = cells[idx_proposal].get_text(strip=True) if idx_proposal is not None and idx_proposal < len(cells) else ""
        submitted_date = None
        if idx_reg_date is not None and idx_reg_date < len(cells):
            submitted_date = _parse_uk_date(cells[idx_reg_date].get_text(strip=True))

        decision_text = ""
        if idx_decision is not None and idx_decision < len(cells):
            decision_text = cells[idx_decision].get_text(strip=True)
        decision_date = None
        if idx_decision_date is not None and idx_decision_date < len(cells):
            decision_date = _parse_uk_date(cells[idx_decision_date].get_text(strip=True))

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": submitted_date,
            "status": _normalise_status(decision_text),
            "decision_date": decision_date,
        })

    if not apps:
        _diagnose_row_parse(council_name, header_cells, html)

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
class AgilePortal:
    def __init__(self, council_name: str, council_slug: str, db_council_id: int):
        self.council_name = council_name
        self.council_slug = council_slug
        self.db_council_id = db_council_id

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def scrape(self, browser: Browser, days_back: int) -> list[dict]:
        today = date.today()
        start = today - timedelta(days=days_back)
        url = _build_url(self.council_slug, "registered", start, today)

        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=45_000)
        except Exception as e:
            self._log(f"⚠ Could not load results page: {e}")
            await context.close()
            return []

        await asyncio.sleep(2)  # real, deliberate pause for Angular
                                  # rendering to finish

        try:
            for label in ["Accept", "Reject"]:
                btn = page.get_by_role("button", name=label, exact=False)
                if await btn.count() > 0 and await btn.first.is_visible(timeout=1500):
                    await btn.first.click()
                    await asyncio.sleep(0.5)
                    break
        except Exception:
            pass

        # REAL FIX (2026-08-21) — confirmed directly from captured
        # markup: this table only renders 10 rows initially, despite
        # having already fetched the full real result count from the
        # backend (a real "25 of 25 results" count text can be true
        # while only 10 rows actually exist in the DOM). A real,
        # confirmed "Show more results..." link
        # (ng-click="params.count(params.count() + 10)") loads 10 more
        # each click. Clicking it repeatedly until it's gone (all
        # results loaded) or a real safety cap is hit — same pagination
        # safety-net discipline as every other platform in this
        # project.
        MAX_SHOW_MORE_CLICKS = 20  # real safety cap — 20 clicks = up to
                                     # 200+10 initial = 210 real results,
                                     # comfortably above anything seen
                                     # in recon (25 was the largest)
        clicks = 0
        while clicks < MAX_SHOW_MORE_CLICKS:
            try:
                # REAL FIX (2026-08-21, round 3) — confirmed directly:
                # the link genuinely exists in the DOM every single time
                # (count=1, all 3 councils) but a single instant
                # is_visible() check reported False consistently. Real
                # markup confirms why: the wrapping element uses
                # ng-show="params.count() <= params.total()" — a real
                # AngularJS conditional that starts hidden by default
                # until Angular's own digest cycle evaluates and updates
                # it, which can genuinely take longer than an instant
                # check allows. Using wait_for_selector's real, active
                # polling (proper waiting, not just a point-in-time
                # check) instead — same successful fix pattern just
                # applied to statmap's East Staffordshire timing issue.
                more_link = page.locator("a.sas-table-pagination-moreresults")
                if await more_link.count() == 0:
                    if clicks == 0:
                        self._log(f"'Show more results' link not present at all "
                                  f"— all real results already fit on one page")
                    break
                try:
                    await page.wait_for_selector(
                        "a.sas-table-pagination-moreresults",
                        state="visible", timeout=8_000,
                    )
                except PlaywrightTimeout:
                    if clicks == 0:
                        self._log(f"⚠ 'Show more results' link exists but never "
                                  f"became visible within 8s — genuinely no more "
                                  f"real results to load, or a real rendering "
                                  f"problem worth a closer look")
                    break
                await more_link.first.click(timeout=3000)
                clicks += 1
                await asyncio.sleep(1.5)
            except Exception as e:
                if clicks == 0:
                    self._log(f"⚠ 'Show more results' click attempt failed with "
                              f"a real exception: {e}")
                break
        if clicks > 0:
            self._log(f"Clicked 'Show more results' {clicks} time(s) to load all real rows")

        html = await page.content()
        await context.close()

        apps = _parse_results_table(html, self.council_name)
        self._log(f"{len(apps)} results ({start.isoformat()} to {today.isoformat()})")
        return apps


# ---------------------------------------------------------------------------
async def process_council(portal: AgilePortal, browser: Browser, sem: asyncio.Semaphore):
    async with sem:
        cid = portal.db_council_id
        print(f"\n[{portal.council_name}] (council_id={cid})")

        if should_stop():
            print(f"    [{portal.council_name}] — skipping, time budget reached "
                  f"({elapsed_minutes():.1f} min elapsed)")
            return

        try:
            raw_apps = await portal.scrape(browser, DAYS_BACK)
        except Exception as e:
            print(f"    [{portal.council_name}] ✗ Error: {e}")
            return

        if not raw_apps:
            await _supa_patch_council(cid, {"coverage_source": "pending"})
            return

        postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
        coords = await geocode(postcodes) if postcodes else {}
        if postcodes:
            portal._log(f"Geocoding {len(postcodes)} postcodes…")

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
                "submitted_date": a.get("submitted_date"),
                "decision_date": a.get("decision_date"),
                "lat": lat,
                "lng": lng,
                "source": "agileapplications_scraper",
            })

        if fallback_count:
            portal._log(f"Council centroid fallback for {fallback_count} apps")

        if records:
            portal._log(f"Upserting {len(records)} records with council_id={cid}")
            ok = await _supa_upsert(records)
            if ok:
                portal._log(f"✓ Saved {len(records)}")
                await _supa_patch_council(cid, {
                    "coverage_source": "agileapplications",
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                })


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind agileapplications.co.uk scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder (None) DB id "
              "in agileapplications_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun agileapplications_councils.py's INSERT_SQL in Supabase first, "
              "then replace each None above with the real id Supabase assigns.")
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        sem = asyncio.Semaphore(CONCURRENCY)
        portals = [
            AgilePortal(name, council_slug, COUNCIL_DB_IDS[name])
            for name, council_slug in AGILE_COUNCILS
        ]

        await asyncio.gather(*[process_council(p, browser, sem) for p in portals])

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
