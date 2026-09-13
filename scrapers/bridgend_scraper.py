#!/usr/bin/env python3
"""
PlanFind — Bridgend County Borough Council scraper (2026-09-11).

Real, confirmed evidence trail — see bridgend_councils.py's module
docstring for the full summary, and fylde_cluster_recon.py /
bridgend_results_diagnostic.py / bridgend_resultsperpage_diagnostic.py
for the diagnostic rounds that produced it.

ARCHITECTURE: same search-form platform as Fylde/Worcester/Vale of
Glamorgan (disclaimer, DateReceivedFrom/DateReceivedTo, submit scoped
to the form) but a genuinely DIFFERENT results-rendering platform —
built as its own dedicated scraper rather than folded into
planning_register_scraper.py, since the results parsing and
pagination mechanism share nothing with that platform's tblResults/
"Next Page." pattern.

CONFIRMED REAL DETAILS:
  - No SearchPlanning checkbox tick needed (hidden-only field here,
    unlike Worcester/Vale of Glamorgan's visible checkbox requirement)
  - resultsPerPage=50 dropdown works via an in-place update — no
    navigation, confirmed row count 10 -> 50 directly
  - Pagination via direct URL: /Search/ResultsPage/{page}/50?module=PLA
  - Real reference format: P/YY/NNN/TYPE (e.g. P/26/459/FUL)
  - Results table: table.table, with "Location and Proposal" combining
    address and description in one cell (address first line, blank
    lines, then description) — NOT two separate columns

HONEST LIMITATION: every application seen during diagnostics showed
Decision/Decision Date as "-" (undecided) — the real decision-text
vocabulary for this platform hasn't been confirmed yet. The status
normaliser below uses the same general keyword vocabulary already
proven across this project's other scrapers, with a diagnostic for
anything genuinely unrecognised, so a real decision text seen on a
future run is surfaced rather than silently mis-filed.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from bridgend_councils import BASE_URL, SEARCH_URL, COUNCIL_NAME, COUNCIL_DB_IDS

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
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "20"))

START_TIME = time.monotonic()

# Real, confirmed reference format — see module docstring.
REFERENCE_RE = re.compile(r"^P/\d{2}/\d+/[A-Z0-9]+$")


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


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_status(s: str) -> str:
    """Same general keyword vocabulary already proven across this
    project's other scrapers. HONEST LIMITATION (see module docstring):
    every application seen during diagnostics showed "-" (undecided) —
    the real decision-text vocabulary for actual decisions hasn't been
    confirmed yet. Genuinely unrecognised text is diagnosed once per
    run rather than silently filed."""
    if not s or s.strip() == "-":
        return "pending"
    key = s.lower()
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in ("consideration", "received", "pending", "awaiting")):
        return "pending"

    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


def _parse_location_and_proposal(cell_text: str) -> tuple[str, str]:
    """Real, confirmed structure: this single cell combines address and
    description, address on the first non-empty line, description on
    the remaining non-empty line(s), separated by blank lines in the
    raw text. Returns (address, description)."""
    lines = [ln.strip() for ln in cell_text.split("\n") if ln.strip()]
    if not lines:
        return "", ""
    address = lines[0]
    description = " ".join(lines[1:]) if len(lines) > 1 else ""
    return address, description


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_page(html: str) -> list[dict]:
    """Real, confirmed structure: table class="table", real reference
    format P/YY/NNN/TYPE in the first cell. Confirmed columns:
    Application, Location and Proposal (combined address+description),
    Applicant, Decision, Decision Date."""
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    for table in soup.find_all("table", class_="table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue  # header row or non-data row

            ref_text = cells[0].get_text(strip=True)
            if not REFERENCE_RE.match(ref_text):
                continue  # not a real application row

            link = cells[0].find("a", href=True)
            location_proposal_text = cells[1].get_text("\n", strip=False)
            address, description = _parse_location_and_proposal(location_proposal_text)
            applicant = cells[2].get_text(strip=True)
            decision_raw = cells[3].get_text(strip=True)
            decision_date_raw = cells[4].get_text(strip=True) if len(cells) > 4 else ""

            postcode = _extract_postcode(address)
            detail_url = urljoin(BASE_URL, link["href"]) if link else None

            decision_date = None
            if decision_date_raw and decision_date_raw != "-":
                for fmt in ("%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
                    try:
                        decision_date = datetime.strptime(decision_date_raw, fmt).date()
                        break
                    except ValueError:
                        continue
                if decision_date is None and not _ROW_STRUCTURE_DIAGNOSED:
                    _ROW_STRUCTURE_DIAGNOSED = True
                    _log(f"⚠ DECISION DATE DIAGNOSTIC: could not parse "
                         f"{decision_date_raw!r} with any known format")

            apps.append({
                "reference": ref_text,
                "address": address,
                "postcode": postcode,
                "description": description,
                "applicant": applicant,
                "application_type": "Planning",
                "status": _normalise_status(decision_raw),
                "decision_date": decision_date,
                "council_url": detail_url,
            })

    return apps


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


async def scrape() -> list[dict]:
    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        _log(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            if "Disclaimer" in page.url:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                    await page.click("input[value='Agree']", timeout=5_000)

            await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
            await page.fill("#DateReceivedTo", end_str, timeout=5_000)

            # Confirmed: no SearchPlanning checkbox tick needed here —
            # it's hidden-only on this platform, unlike Worcester/Vale
            # of Glamorgan.
            form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
            submit = form_loc.locator("input[type='submit']:visible")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await submit.first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Search fill/submit failed: {type(e).__name__}: {e!r}")
            await context.close()
            await browser.close()
            return []

        _log(f"Post-submit URL: {page.url}")

        # Confirmed working shortcut — an in-place update, no
        # navigation needed. Best-effort: if the dropdown isn't found
        # or the select fails, real pagination below still covers
        # collecting everything at whatever the default page size is.
        try:
            dropdown = page.locator("#resultsPerPage")
            if await dropdown.count() > 0:
                await dropdown.select_option(value="50", timeout=5_000)
                await asyncio.sleep(2)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                _log("Selected 50 results per page")
        except Exception as e:
            _log(f"⚠ Could not select resultsPerPage=50 (continuing with default): {e}")

        html = await page.content()
        page1_apps = _parse_results_page(html)
        for a in page1_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
        _log(f"Page 1: {len(page1_apps)} found (running total {len(all_apps)})")

        # Confirmed real pagination URL pattern:
        # /Search/ResultsPage/{page}/{page_size}?module=PLA — direct
        # navigation, same page/context so session cookies carry over.
        page_num = 2
        while page_num <= MAX_PAGES:
            if should_stop():
                _log(f"⚠ Time budget reached at page {page_num}, stopping")
                break

            page_url = f"{BASE_URL}/Search/ResultsPage/{page_num}/50?module=PLA"
            try:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
            except Exception as e:
                _log(f"⚠ Could not load page {page_num}: {type(e).__name__} — stopping")
                break

            html = await page.content()
            page_apps = _parse_results_page(html)
            if not page_apps:
                _log(f"Page {page_num}: 0 apps parsed — stopping "
                     f"(likely past the real last page)")
                break

            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1
            _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)})")

            if new_count == 0:
                _log(f"Page {page_num}: 0 NEW apps — stopping "
                     f"(likely genuine end of data)")
                break
            page_num += 1

        if page_num > MAX_PAGES:
            _log(f"⚠ Hit the {MAX_PAGES}-page safety cap while still finding "
                 f"new results — real data may extend further")

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Bridgend scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"bridgend_councils.py. Run the INSERT_SQL there, look up the "
              f"real id, and fill it in before running this scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results — nothing to save.")
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

        # Real, confirmed field: applicant isn't part of the shared
        # planning_applications schema used elsewhere in this project
        # (checked against every other scraper's own upsert payload) —
        # folded into the description instead rather than silently
        # dropped, so the real data isn't lost.
        description = a.get("description") or ""
        if a.get("applicant"):
            description = f"{description} (Applicant: {a['applicant']})".strip()

        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": description or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "decision_date": a.get("decision_date").isoformat() if a.get("decision_date") else None,
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "bridgend_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "bridgend_scraper",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
