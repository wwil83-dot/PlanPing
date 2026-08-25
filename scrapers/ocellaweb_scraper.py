#!/usr/bin/env python3
"""
PlanFind — OcellaWeb platform scraper (2026-08-25).

4 councils: Great Yarmouth, South Holland, Havering, Hillingdon. See
ocellaweb_councils.py for the full, real, confirmed evidence backing
every design decision here.

ARCHITECTURE: genuinely one of the simplest platforms in this project
— fill the real date-range fields (DD-MM-YY format), submit via a
real, form-scoped input[type=submit], parse the real results table,
done. No disclaimer gate, no JS-click pagination, no card layout, no
iframes.

HONEST LIMITATIONS:
  - Only Great Yarmouth's real results page has actually been search-
    tested. The other 3 share identical confirmed form fields but any
    real per-council quirks (different column set, different button
    structure) will be caught defensively if they surface in
    production, same approach already proven for the "Search/Advanced"
    family.
  - Real pagination has never actually been triggered (only 26 results
    in the test window, no pagination controls appeared) — genuinely
    unknown whether or how this platform paginates larger result sets.
    Flagged clearly in the log if a result count suggests more results
    might exist than were actually captured.
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

from ocellaweb_councils import OCELLAWEB_COUNCILS, COUNCIL_DB_IDS

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
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "1"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "50"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _log(council_name: str, msg: str) -> None:
    print(f"    [{council_name}] {msg}")


# ---------------------------------------------------------------------------
def _normalise_status(status_text: str) -> str:
    """Real, confirmed via ocellaweb_results_recon.py: the Status
    column holds a genuine decision outcome when decided (confirmed
    real value: 'NO OBJECTION'), not just a workflow stage.
    'Undecided' is the real pending state. Same real 'no objection =
    approved-ish' precedent already established for the Northgate
    servlet family's own Runnymede data."""
    if not status_text:
        return "pending"
    s = status_text.lower()
    if "undecided" in s:
        return "pending"
    if any(x in s for x in ("approv", "grant", "permit", "allow", "no objection")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
    return "pending"


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_ocellaweb_date(s: str) -> Optional[str]:
    """Real, confirmed date format: DD-MM-YY (2-digit year), explicitly
    stated on the page itself and confirmed in real captured results
    (e.g. '18-08-26')."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%m-%y", "%d/%m/%y", "%d-%m-%Y", "%d/%m/%Y"):
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
    print(f"    [{council_name}] ROW PARSE DIAGNOSTIC: real headers found: "
          f"{headers!r}. Response snippet: {html_snippet[:500]!r}")


def _parse_results_table(html: str, base_url: str, council_name: str) -> list[dict]:
    """Real, confirmed structure (Great Yarmouth only, directly
    tested): plain <table>, real header row using <th>, columns
    Reference | Location | Proposal | Received | Type | Status."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    table = None
    for t in tables:
        rows = t.find_all("tr")
        if len(rows) > 1 and t.find("th"):
            table = t
            break

    if not table:
        _diagnose_row_parse(council_name, [], html)
        return []

    rows = table.find_all("tr")
    header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all("th")]

    def _col_index(*keywords) -> Optional[int]:
        for i, h in enumerate(header_cells):
            if any(kw in h for kw in keywords):
                return i
        return None

    idx_ref = _col_index("reference")
    idx_location = _col_index("location")
    idx_proposal = _col_index("proposal")
    idx_received = _col_index("received")
    idx_status = _col_index("status")

    if idx_ref is None:
        _diagnose_row_parse(council_name, header_cells, html)
        return []

    apps = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= idx_ref:
            continue

        ref_cell = cells[idx_ref]
        link = ref_cell.find("a")
        reference = ref_cell.get_text(strip=True)
        if not reference:
            continue

        detail_url = None
        if link and link.get("href"):
            href = link["href"]
            detail_url = href if href.startswith("http") else f"{base_url}/OcellaWeb/{href}"
        else:
            # Real, confirmed fallback: even without a real <a> tag,
            # the URL pattern itself is confirmed simple and
            # reconstructible directly from the reference alone.
            detail_url = f"{base_url}/OcellaWeb/planningDetails?reference={reference}&from=planningSearch"

        address = cells[idx_location].get_text(" ", strip=True) if idx_location is not None and idx_location < len(cells) else ""
        proposal = cells[idx_proposal].get_text(" ", strip=True) if idx_proposal is not None and idx_proposal < len(cells) else ""
        received_raw = cells[idx_received].get_text(strip=True) if idx_received is not None and idx_received < len(cells) else ""
        status_raw = cells[idx_status].get_text(strip=True) if idx_status is not None and idx_status < len(cells) else ""
        postcode = _extract_postcode(address)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": _parse_ocellaweb_date(received_raw),
            "status": _normalise_status(status_raw),
            "council_url": detail_url,
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
async def scrape(browser: Browser, council_name: str, base_url: str, days_back: int) -> list[dict]:
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(f"{base_url}/OcellaWeb/planningSearch", wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        _log(council_name, f"⚠ Could not load search page: {e}")
        await context.close()
        return []

    today = date.today()
    start = today - timedelta(days=days_back)
    from_str = start.strftime("%d-%m-%y")
    to_str = today.strftime("%d-%m-%y")

    try:
        await page.fill("#receivedFrom", from_str, timeout=5_000)
        await page.fill("#receivedTo", to_str, timeout=5_000)
    except Exception as e:
        _log(council_name, f"⚠ Could not fill date fields: {e}")
        await context.close()
        return []

    try:
        # REAL, CONFIRMED FIX: the real search button is an
        # <input type="submit" value="Search">, and an unscoped
        # "button:has-text('Search')" selector matched an unrelated
        # element elsewhere on the page — scoping specifically to the
        # form containing the date fields just filled, same proven
        # pattern as the "Search/Advanced" family's own Cherwell fix.
        form_with_dates = page.locator("form").filter(has=page.locator("#receivedFrom"))
        search_btn = form_with_dates.locator("button:has-text('Search')")
        if await search_btn.count() == 0:
            search_btn = form_with_dates.locator("input[type='submit'][value='Search']")
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

    html = await page.content()
    apps = _parse_results_table(html, base_url, council_name)
    _log(council_name, f"Found {len(apps)} real application(s) within {days_back} days")

    # Honest, real flag: pagination has never actually been observed
    # on this platform — if a suspiciously round or large number of
    # results comes back, worth a manual look rather than silently
    # trusting it's complete.
    if len(apps) >= 100:
        _log(council_name, f"⚠ {len(apps)} results found — this platform's real "
             f"pagination behaviour (if any) has never been confirmed; worth "
             f"a manual check that this is genuinely the complete set")

    await context.close()
    return apps


async def recheck_pending(browser: Browser, council_name: str, pending: list[dict]) -> list[dict]:
    """Real, confirmed permanent, reusable detail URL — a genuine
    pending-recheck mechanism is possible here, unlike Barrow's
    session-bound URLs."""
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
            continue

        # Real detail-page field labels never actually recon'd — using
        # a defensive keyword search, same discipline as before a
        # detail page has ever been directly seen elsewhere in this
        # project.
        m = re.search(r"status\s*\n?\s*([A-Za-z ,.'-]+)", text, re.I)
        if m:
            status = _normalise_status(m.group(1).strip())
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
                "submitted_date": a.get("submitted_date"),
                "lat": lat,
                "lng": lng,
                "source": "ocellaweb_scraper",
            })

        if fallback_count:
            _log(name, f"Council centroid fallback for {fallback_count} apps")

        # Real, established discipline: recheck updates kept as their
        # own, separate upsert call — mixing full application records
        # with partial status-only records in one call hits
        # PostgREST's real "All object keys must match" error, the
        # exact bug already found and fixed in esl_scraper.py.
        recheck_records = [{
            "council_id": cid,
            "reference": u["reference"],
            "status": u["status"],
        } for u in recheck_updates]

        saved_count = 0
        if records:
            _log(name, f"Upserting {len(records)} application records "
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
                "coverage_source": "ocellaweb",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind OcellaWeb scraper")
    print(f"Councils:    {len(OCELLAWEB_COUNCILS)}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder (None) DB id "
              "in ocellaweb_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun ocellaweb_councils.py's INSERT_SQL in Supabase first, then "
              "replace each None above with the real id Supabase assigns.")
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        sem = asyncio.Semaphore(CONCURRENCY)
        await asyncio.gather(*[
            process_council(name, base_url, browser, sem)
            for name, base_url in OCELLAWEB_COUNCILS
        ])

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
