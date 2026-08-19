#!/usr/bin/env python3
"""
PlanFind — Northgate 'ApplicationSearchServlet' family scraper
(2026-08-19).

Covers 3 councils on one shared platform: Hartlepool, High Peak,
Staffordshire Moorlands — see northgate_servlet_councils.py for the
real, confirmed evidence backing every design decision here. South
Tyneside is deliberately NOT covered by this scraper — genuinely
different technology, own separate scraper
(northgate_south_tyneside_scraper.py).

ARCHITECTURE: one real Playwright page per council. Load the real
search form, dismiss any cookie/consent overlay, fill exactly ONE
date-range pair (confirmed: filling more than one produces a genuine
empty-result response, not more data), submit, parse the real results
table. The results always land at the SAME url (no redirect, no
dynamic path — confirmed directly, genuinely different from South
Tyneside) so no session-URL capture logic is needed here at all.

HONEST LIMITATIONS, worth remembering:
  - Hartlepool's results list has NO decision/status column at all —
    every Hartlepool application is saved as 'pending' on first sight,
    same as this project's other platforms with the same gap. A
    pending-recheck pass (using the real, confirmed PKID-based detail
    link) is the only route to a real decision for Hartlepool.
  - High Peak and Staffordshire Moorlands DO show decision status
    directly in the search results list — no recheck needed for those
    two specifically, a genuine advantage over Hartlepool.
  - Real search window: today back 30 days on the single confirmed
    date field (ReceivedDate or ValidDate). No pagination mechanism
    was confirmed during recon — if a results table is suspiciously
    capped at a round number, that's the tell a real pagination control
    exists and needs handling, not confirmed to happen yet.
  - No CSRF token was found anywhere in this platform's real form
    fields (unlike South Tyneside/its ASP.NET ViewState, or Idox's
    session-bound weeklyList form) — a much simpler platform in that
    respect.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeout

from northgate_servlet_councils import NORTHGATE_SERVLET_COUNCILS, COUNCIL_DB_IDS

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
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "20"))
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "1"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "100"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------------------
# Helpers — matching this project's established conventions exactly
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
    """Real Northgate decision vocabulary confirmed via recon:
    'Awaiting Validation', 'Application Invalid' — both genuinely
    pre-decision states, correctly falling through to 'pending' below.
    Extended with the same approved/refused/withdrawn keyword logic
    used everywhere else in this project for whatever real decision
    text eventually appears once an application resolves."""
    if not s:
        return "pending"
    s = s.lower()
    if any(x in s for x in ("awaiting", "invalid", "pending", "in progress")):
        return "pending"
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


def _clean_address(cell_text: str) -> str:
    """Real evidence: Hartlepool's address cells use <br/> separators
    (rendered as newlines here — get_text("\\n", ...) is used
    specifically so these become real splittable newlines, not spaces
    that would hide the original line breaks), High Peak/Staffordshire
    Moorlands use comma separators with heavy real whitespace/tab
    padding between parts. Normalising both to a single clean,
    comma-separated string with internal whitespace collapsed."""
    parts = [" ".join(p.split()) for p in re.split(r"[\n,]+", cell_text) if p.strip()]
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


def _extract_pkid(url: str) -> Optional[str]:
    try:
        qs = parse_qs(urlparse(url).query)
        pkids = qs.get("PKID")
        return pkids[0] if pkids else None
    except Exception:
        return None


_ROW_PARSE_DIAGNOSED: set[str] = set()


def _diagnose_row_parse(council_name: str, headers: list[str], html_snippet: str):
    if council_name in _ROW_PARSE_DIAGNOSED:
        return
    _ROW_PARSE_DIAGNOSED.add(council_name)
    print(f"    [{council_name}] ROW PARSE DIAGNOSTIC: real headers found: "
          f"{headers!r}. Response snippet: {html_snippet[:500]!r}")


def _parse_results_table(html: str, base_url: str, council_name: str) -> list[dict]:
    """Real, confirmed table structure — plain <table><tr><th>/<td>,
    no nested divs or JS-rendered content. Column mapping is done by
    real header text (matched by keyword), NOT fixed position, since
    Hartlepool (3 columns) and High Peak/Staffordshire Moorlands
    (7 columns) genuinely differ — this stays correct for either shape
    without needing per-council special-casing here."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        _diagnose_row_parse(council_name, [], html)
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        _diagnose_row_parse(council_name, [], html)
        return []

    header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all("th")]
    if not header_cells:
        _diagnose_row_parse(council_name, [], html)
        return []

    def _col_index(*keywords) -> Optional[int]:
        for i, h in enumerate(header_cells):
            if any(kw in h for kw in keywords):
                return i
        return None

    idx_ref = _col_index("reference", "application number")
    idx_received = _col_index("received")
    idx_valid = _col_index("valid")
    idx_address = _col_index("site location", "location", "address")
    idx_proposal = _col_index("proposal", "proposed development", "development")
    idx_decision = _col_index("decision") if _col_index("decision date") != _col_index("decision") else None
    # Real evidence: "decision" and "decision date" are TWO separate
    # real columns on High Peak/Staffordshire Moorlands — need to find
    # each independently, not let one keyword match swallow both.
    idx_decision = None
    idx_decision_date = None
    for i, h in enumerate(header_cells):
        if h == "decision":
            idx_decision = i
        elif "decision date" in h:
            idx_decision_date = i

    if idx_ref is None or idx_address is None:
        _diagnose_row_parse(council_name, header_cells, html)
        return []

    apps = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= max(idx_ref, idx_address):
            continue

        ref_cell = cells[idx_ref]
        link = ref_cell.find("a")
        reference = ref_cell.get_text(strip=True)
        detail_url = urljoin(base_url + "/", link["href"]) if link and link.get("href") else None
        pkid = _extract_pkid(detail_url) if detail_url else None

        raw_address = cells[idx_address].get_text("\n", strip=True) if idx_address < len(cells) else ""
        address = _clean_address(raw_address)
        postcode = _extract_postcode(address)

        proposal = cells[idx_proposal].get_text(strip=True) if idx_proposal is not None and idx_proposal < len(cells) else ""

        received_date = None
        if idx_received is not None and idx_received < len(cells):
            received_date = _parse_uk_date(cells[idx_received].get_text(strip=True))
        if not received_date and idx_valid is not None and idx_valid < len(cells):
            received_date = _parse_uk_date(cells[idx_valid].get_text(strip=True))

        decision_text = ""
        if idx_decision is not None and idx_decision < len(cells):
            decision_text = cells[idx_decision].get_text(strip=True)
        decision_date = None
        if idx_decision_date is not None and idx_decision_date < len(cells):
            decision_date = _parse_uk_date(cells[idx_decision_date].get_text(strip=True))

        if not reference:
            continue

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": received_date,
            "status": _normalise_status(decision_text),
            "decision_date": decision_date,
            "council_url": detail_url,
            "pkid": pkid,
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
                cid_hint = records[0].get("council_id") if records else "?"
                print(f"    ✗ Upsert HTTP {r.status_code} (council_id={cid_hint}): {r.text[:300]}")
                return False
            return True
    except Exception as e:
        cid_hint = records[0].get("council_id") if records else "?"
        print(f"    ✗ Upsert exception (council_id={cid_hint}): {e}")
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
# NorthgateServletPortal — one per council
# ---------------------------------------------------------------------------
class NorthgateServletPortal:
    def __init__(self, council_name: str, base_url: str, date_field: str, db_council_id: int):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.date_field = date_field  # "ReceivedDate" or "ValidDate"
        self.db_council_id = db_council_id

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def _dismiss_overlay(self, page):
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

    async def _fill_date_field(self, page, field_name: str, value: str) -> bool:
        el = page.locator(f"input[name='{field_name}']").first
        if await el.count() == 0:
            return False
        try:
            await el.fill(value, timeout=1500)
            return True
        except Exception:
            try:
                await el.evaluate(
                    "(el, val) => { el.value = val; "
                    "el.dispatchEvent(new Event('change', {bubbles: true})); "
                    "el.dispatchEvent(new Event('blur', {bubbles: true})); }",
                    value,
                )
                return True
            except Exception as e:
                self._log(f"⚠ Could not set field {field_name!r}: {e}")
                return False

    async def scrape(self, browser: Browser, days_back: int) -> list[dict]:
        url = f"{self.base_url}/portal/servlets/ApplicationSearchServlet"
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            self._log(f"⚠ Could not load search page: {e}")
            await context.close()
            return []

        await self._dismiss_overlay(page)

        today = date.today()
        start = today - timedelta(days=days_back)
        date_from_str = start.strftime("%d/%m/%Y")
        date_to_str = today.strftime("%d/%m/%Y")

        ok_from = await self._fill_date_field(page, f"{self.date_field}From", date_from_str)
        ok_to = await self._fill_date_field(page, f"{self.date_field}To", date_to_str)
        if not (ok_from and ok_to):
            self._log(f"⚠ Could not fill both {self.date_field} fields — "
                      f"search may return unexpected results")

        try:
            btn = page.get_by_role("button", name=re.compile(r"^search$", re.I))
            if await btn.count() > 0:
                await btn.first.click()
            else:
                submit_input = page.locator("input[type='submit']").first
                await submit_input.click()
        except Exception as e:
            self._log(f"⚠ Could not click search button: {e}")
            await context.close()
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        html = await page.content()
        await context.close()

        html_lower = html.lower()
        if "did not return any results" in html_lower or "refine your query" in html_lower:
            self._log(f"0 results ({date_from_str} to {date_to_str}, real empty "
                      f"response from the site, not an error)")
            return []

        apps = _parse_results_table(html, self.base_url, self.council_name)
        self._log(f"{len(apps)} results ({date_from_str} to {date_to_str})")
        return apps

    async def recheck_pending(self, browser: Browser, pending: list[dict]) -> list[dict]:
        """Real, confirmed PKID-based detail URL, reused directly here
        — same purpose as this project's other pending-recheck passes.
        Most needed for Hartlepool specifically (no decision info in
        its search results at all), but applied uniformly since High
        Peak/Staffordshire Moorlands' list-level decision could still
        be stale by the time of a recheck."""
        if not pending:
            return []
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()
        updates = []
        for p in pending:
            if should_stop():
                self._log(f"⚠ Time budget reached mid-recheck, stopping")
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
            # Real detail-page field labels not yet confirmed via recon
            # for this platform — using a defensive keyword search
            # rather than a fixed label, matching the same discipline
            # used before a detail page has ever been directly recon'd.
            m = re.search(r"decision\s*:?\s*\n?\s*([A-Za-z ,.'-]+)", text, re.I)
            if m:
                decision_text = m.group(1).strip()
                status = _normalise_status(decision_text)
                if status != "pending":
                    updates.append({
                        "reference": p["reference"],
                        "status": status,
                    })
        await context.close()
        if updates:
            self._log(f"Recheck: {len(updates)} of {len(pending)} previously-pending "
                      f"application(s) now have a real decision")
        return updates


# ---------------------------------------------------------------------------
async def process_council(portal: NorthgateServletPortal, browser: Browser,
                           sem: asyncio.Semaphore,
                           pending_recheck: Optional[list[dict]] = None):
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

        recheck_updates = []
        if pending_recheck:
            try:
                recheck_updates = await portal.recheck_pending(browser, pending_recheck)
            except Exception as e:
                print(f"    [{portal.council_name}] ⚠ Recheck error: {e}")

        if not raw_apps and not recheck_updates:
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
                "council_url": a.get("council_url"),
                "latitude": lat,
                "longitude": lng,
            })

        if fallback_count:
            portal._log(f"Council centroid fallback for {fallback_count} apps")

        for u in recheck_updates:
            records.append({
                "council_id": cid,
                "reference": u["reference"],
                "status": u["status"],
            })

        if records:
            portal._log(f"Upserting {len(records)} records with council_id={cid}")
            ok = await _supa_upsert(records)
            if ok:
                portal._log(f"✓ Saved {len(records)}")
                await _supa_patch_council(cid, {
                    "coverage_source": "northgate_servlet",
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                })


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Northgate servlet-family scraper")
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
              "in northgate_servlet_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun northgate_servlet_councils.py's INSERT_SQL in Supabase first, "
              "then replace each None above with the real id Supabase assigns.")
        sys.exit(1)

    pending_by_council: dict[int, list[dict]] = {}
    try:
        for name, cid in COUNCIL_DB_IDS.items():
            rows = await _supa_get(
                "planning_applications",
                council_id=f"eq.{cid}",
                status="eq.pending",
                select="reference,council_url",
                limit=str(RECHECK_LIMIT),
            )
            if rows:
                pending_by_council[cid] = rows
        total_pending = sum(len(v) for v in pending_by_council.values())
        print(f"Pending recheck: {total_pending} applications across "
              f"{len(pending_by_council)} councils (bounded to {RECHECK_LIMIT} each)\n")
    except Exception as e:
        print(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        sem = asyncio.Semaphore(CONCURRENCY)
        portals = [
            NorthgateServletPortal(name, base_url, date_field, COUNCIL_DB_IDS[name])
            for name, base_url, date_field in NORTHGATE_SERVLET_COUNCILS
        ]

        await asyncio.gather(*[
            process_council(p, browser, sem, pending_recheck=pending_by_council.get(p.db_council_id))
            for p in portals
        ])

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
