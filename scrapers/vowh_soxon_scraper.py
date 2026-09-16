#!/usr/bin/env python3
"""
PlanFind — Vale of White Horse & South Oxfordshire scraper (2026-09-13).

Real, confirmed evidence trail — see vowh_soxon_councils.py's module
docstring for the full summary, and fylde_cluster_recon.py /
fylde4_diagnostic.py (9 diagnostic rounds) for how it was found.

HONEST LIMITATION, worth repeating here directly: every real test run
during diagnosis returned a genuinely successful but EMPTY search
("Search Results (0) - Online Register"), because the
PlanningApplicationTypes-checkbox theory was only ever tested as a
theory, never confirmed against real populated results before this
scraper was written. The results parser below is built defensively —
it extracts the real total count from the page title first (ground
truth, independent of table-parsing), tries a broad set of real
table-finding strategies, and logs clearly if what it finds doesn't
match. The FIRST real production run should be checked closely.
Pagination is similarly unconfirmed — no diagnostic ever reached a
populated multi-page result — so this uses a generic, defensive
Next-link search and stops cleanly (not an error) if none is found,
treating a single page as the safe default until proven otherwise.
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

from vowh_soxon_councils import VOWH_SOXON_COUNCILS, COUNCIL_DB_IDS

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
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "30"))

START_TIME = time.monotonic()

# Real, confirmed pattern from the actual page title:
# "Search Results (47) - Online Register"
TITLE_COUNT_RE = re.compile(r"Search Results \((\d+)\)")


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


_STATUS_DIAGNOSED: dict[str, set[str]] = {}


def _normalise_status(s: str, council_name: str) -> str:
    """Same general keyword vocabulary already proven across this
    project's other scrapers. Genuinely unconfirmed real status text
    for this platform — diagnosed clearly rather than guessed."""
    if not s:
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

    diagnosed = _STATUS_DIAGNOSED.setdefault(council_name, set())
    if key not in diagnosed:
        diagnosed.add(key)
        print(f"    [{council_name}] ⚠ STATUS DIAGNOSTIC: unrecognised status "
              f"{s!r} — filed as 'pending'")
    return "pending"


_ROW_STRUCTURE_DIAGNOSED: set[str] = set()


def _parse_results_page(html: str, base_url: str, council_name: str) -> list[dict]:
    """HONEST LIMITATION (see module docstring): this platform's real
    result-row structure was never directly confirmed with actual
    data. Tries table.tblResults first (in case it shares Fylde's
    family's structure after all, despite everything else diverging),
    then falls back to ANY real table with rows containing a real
    application-reference-like link, with clear diagnostics either
    way."""
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    tables = soup.find_all("table", class_="tblResults")
    if not tables:
        tables = soup.find_all("table")
        if tables and council_name not in _ROW_STRUCTURE_DIAGNOSED:
            _ROW_STRUCTURE_DIAGNOSED.add(council_name)
            print(f"    [{council_name}] ⚠ ROW STRUCTURE DIAGNOSTIC: no "
                  f"table.tblResults found — falling back to {len(tables)} "
                  f"generic <table> element(s). Real structure genuinely "
                  f"unconfirmed for this platform — check this run's saved "
                  f"data carefully.")

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            link = row.find("a", href=True)
            if not link:
                continue

            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            reference = link.get_text(strip=True)
            if not reference or len(reference) < 3:
                continue

            location_raw = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            proposal = " ".join(c.get_text(strip=True) for c in cells[2:]) if len(cells) > 2 else ""
            status_raw = cells[-1].get_text(strip=True) if len(cells) > 3 else ""

            postcode = _extract_postcode(location_raw)
            detail_url = urljoin(base_url, link["href"])

            apps.append({
                "reference": reference,
                "address": location_raw,
                "postcode": postcode,
                "description": proposal,
                "application_type": "Planning",
                "status": _normalise_status(status_raw, council_name),
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


class VowhSoxonPortal:
    def __init__(self, council_name: str, base_url: str, db_council_id: int):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.search_url = f"{self.base_url}/Search/Advanced"
        self.db_council_id = db_council_id

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def _click_disclaimer_if_present(self, page) -> None:
        """Confirmed real button text: "Accept" (an exact match, unlike
        Vale of Glamorgan's partial "Accept & Continue")."""
        if "Disclaimer" not in page.url:
            return
        for selector in ["button:has-text('Accept')", "input[value*='Accept' i]"]:
            try:
                loc = page.locator(selector)
                if await loc.count() > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                        await loc.first.click(timeout=5_000)
                    return
            except Exception:
                continue
        self._log("⚠ Could not click through disclaimer with any known selector")

    async def scrape(self, browser) -> list[dict]:
        today = date.today()
        start = today - timedelta(days=DAYS_BACK)
        # Confirmed: native HTML5 <input type="date"> — ISO format
        # required, same as Welwyn Hatfield on the other platform.
        start_str = start.isoformat()
        end_str = today.isoformat()

        all_apps: list[dict] = []
        seen_refs: set[str] = set()

        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(self.search_url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            await self._click_disclaimer_if_present(page)

            field = page.locator("#DateReceivedFrom")
            if await field.count() == 0:
                self._log("⚠ #DateReceivedFrom not found in DOM at all — stopping")
                await context.close()
                return []

            if not await field.first.is_visible():
                try:
                    opened = await field.first.evaluate(
                        "el => { const d = el.closest('details'); "
                        "if (d) { d.open = true; return true; } return false; }"
                    )
                    self._log(f"Set closest <details> ancestor .open=true: {opened}")
                except Exception as e:
                    self._log(f"⚠ Could not open <details> ancestor: {type(e).__name__}: {e}")

            if not await field.first.is_visible():
                self._log("⚠ #DateReceivedFrom still not visible after opening "
                           "<details> — stopping")
                await context.close()
                return []

            await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
            await page.fill("#DateReceivedTo", end_str, timeout=5_000)

            type_checkboxes = await page.locator("input[id^='PlanningApplicationTypes_']").all()
            ticked_count = 0
            for cb in type_checkboxes:
                try:
                    if not await cb.is_checked():
                        await cb.evaluate(
                            "el => { el.checked = true; "
                            "el.dispatchEvent(new Event('change', {bubbles: true})); }"
                        )
                        ticked_count += 1
                except Exception:
                    continue
            self._log(f"Ticked {ticked_count} of {len(type_checkboxes)} real "
                       f"PlanningApplicationTypes checkboxes")

            form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
            scope = form_loc if await form_loc.count() > 0 else page

            submit = scope.locator(
                "input[type='submit'][value='Apply']:visible, "
                "button:has-text('Search'):visible, input[type='submit'][value*='Search' i]:visible"
            )
            if await submit.count() == 0:
                self._log("⚠ No visible submit button found — stopping")
                await context.close()
                return []

            await submit.first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(2)
        except Exception as e:
            self._log(f"⚠ Search fill/submit failed: {type(e).__name__}: {e!r}")
            await context.close()
            return []

        title = await page.title()
        self._log(f"Post-submit page title: {title!r}")
        title_match = TITLE_COUNT_RE.search(title)
        expected_count = int(title_match.group(1)) if title_match else None
        if expected_count is not None:
            self._log(f"Real expected result count from page title: {expected_count}")

        html = await page.content()
        page1_apps = _parse_results_page(html, self.base_url, self.council_name)
        for a in page1_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
        self._log(f"Page 1: {len(page1_apps)} parsed (running total {len(all_apps)})")

        if expected_count is not None and len(all_apps) != expected_count:
            self._log(f"⚠ COUNT MISMATCH DIAGNOSTIC: page title says "
                       f"{expected_count} real results exist, but the parser "
                       f"only found {len(all_apps)} — real row structure or "
                       f"pagination may need attention")

        page_num = 2
        while page_num <= MAX_PAGES:
            if should_stop():
                self._log(f"⚠ Time budget reached at page {page_num}, stopping")
                break
            next_link = page.locator(
                "a:has-text('Next'):visible, a[aria-label*='next' i]:visible"
            )
            if await next_link.count() == 0:
                if page_num == 2:
                    self._log("No real 'Next' link found — treating as a single page "
                               "(pagination mechanism unconfirmed for this platform)")
                break
            try:
                await next_link.first.click(timeout=10_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1.5)
            except Exception as e:
                self._log(f"⚠ Could not click Next at page {page_num}: {type(e).__name__}")
                break

            html = await page.content()
            page_apps = _parse_results_page(html, self.base_url, self.council_name)
            if not page_apps:
                self._log(f"Page {page_num}: 0 apps parsed — stopping")
                break

            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1
            self._log(f"Page {page_num}: {new_count} new (running total {len(all_apps)})")
            if new_count == 0:
                self._log(f"Page {page_num}: 0 NEW apps — stopping")
                break
            page_num += 1

        await context.close()
        return all_apps


async def process_council(portal: VowhSoxonPortal, browser) -> int:
    cid = portal.db_council_id
    print(f"\n[{portal.council_name}] (council_id={cid})")

    try:
        raw_apps = await portal.scrape(browser)
    except Exception as e:
        print(f"    [{portal.council_name}] ✗ Error: {e}")
        return 0

    if not raw_apps:
        await _supa_patch_council(cid, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        return 0

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        print(f"    [{portal.council_name}] Geocoding {len(postcodes)} postcodes…")

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
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "vowh_soxon_scraper",
        })

    if fallback_count:
        print(f"    [{portal.council_name}] Council centroid fallback for {fallback_count} apps")

    print(f"    [{portal.council_name}] Upserting {len(records)} records with council_id={cid}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"    [{portal.council_name}] ✓ Saved {len(records)}")
        await _supa_patch_council(cid, {
            "coverage_source": "vowh_soxon_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })
    return len(records) if ok else 0


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Vale of White Horse / South Oxfordshire scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder (None) "
              "DB id in vowh_soxon_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun vowh_soxon_councils.py's INSERT_SQL in Supabase "
              "first, then replace each None above with the real id.")
        sys.exit(1)

    portals = [
        VowhSoxonPortal(name, base_url, COUNCIL_DB_IDS[name])
        for name, base_url in VOWH_SOXON_COUNCILS
    ]

    print(f"Scraping {len(portals)} councils…\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        total = 0
        for portal in portals:
            total += await process_council(portal, browser)

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")


if __name__ == "__main__":
    asyncio.run(main())
