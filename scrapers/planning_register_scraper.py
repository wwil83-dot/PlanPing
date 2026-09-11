#!/usr/bin/env python3
"""
PlanFind — planning-register platform family scraper (2026-09-11).

Covers councils confirmed via fylde_cluster_recon.py to run the exact
same underlying platform as Fylde Council — see
planning_register_councils.py for the full real-evidence trail behind
each entry in PLANNING_REGISTER_COUNCILS.

REAL, CONFIRMED SEARCH FLOW (differs from Fylde's own in one genuine
way): both councils need the SearchPlanning checkbox explicitly ticked
before submission succeeds — Fylde's own form doesn't require this.
Confirmed via direct testing: without ticking it, submission times
out; with it ticked, a real results page loads.

Vale of Glamorgan additionally has a disclaimer gate (Worcester does
not) — confirmed real button text "Accept & Continue", a partial-text
match, not an exact "Accept"/"Agree".

HONEST LIMITATION: unlike fylde_scraper.py, this does NOT split
results into Planning vs Building Control via a confirmed href prefix
— that specific row-level structure was never directly confirmed for
these two councils (the recon only checked page-level fingerprints:
table class, pagination). Every application found is currently treated
as a Planning application, matching what was actually searched
(SearchPlanning ticked; other categories not ticked). If a future run
reveals results containing other categories mixed in, the
ROW STRUCTURE DIAGNOSTIC below will surface it rather than silently
mis-tagging them.
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

from planning_register_councils import PLANNING_REGISTER_COUNCILS, COUNCIL_DB_IDS

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
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "50"))

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


_STATUS_DIAGNOSED: dict[str, set[str]] = {}


def _normalise_status(s: str, council_name: str) -> str:
    """Reused logic from fylde_scraper.py's proven status vocabulary —
    same confirmed platform, very likely the same real status text,
    but diagnosed per-council in case it differs."""
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
    """Real, confirmed structure: table class="tblResults" — same as
    Fylde's own. HONEST LIMITATION (see module docstring): the href
    prefix distinguishing Planning from other categories was NOT
    directly confirmed for these councils, unlike Fylde's own
    /Planning/Display/ split. Every row found is parsed generically
    and tagged "Planning" (matching what was actually searched), with
    a one-time diagnostic if the row shape looks unexpected."""
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    for table in soup.find_all("table", class_="tblResults"):
        rows = table.find_all("tr")
        for row in rows:
            link = row.find("a", href=True)
            if not link:
                continue  # header row or non-data row

            cells = row.find_all("td")
            if len(cells) < 4:
                if council_name not in _ROW_STRUCTURE_DIAGNOSED:
                    _ROW_STRUCTURE_DIAGNOSED.add(council_name)
                    row_text = row.get_text(" | ", strip=True)
                    print(f"    [{council_name}] ⚠ ROW STRUCTURE DIAGNOSTIC: a row had "
                          f"only {len(cells)} <td> cells (expected >= 4, matching "
                          f"Fylde's own confirmed shape). Raw row text: {row_text!r}")
                continue

            reference = link.get_text(strip=True)
            if not reference or len(reference) < 3:
                continue

            location_raw = cells[1].get_text(strip=True)
            proposal = cells[2].get_text(strip=True)
            status_raw = cells[3].get_text(strip=True)

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


class PlanningRegisterPortal:
    def __init__(self, council_name: str, base_url: str, needs_disclaimer: bool,
                 db_council_id: int):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.search_url = f"{self.base_url}/Search/Advanced"
        self.needs_disclaimer = needs_disclaimer
        self.db_council_id = db_council_id

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def _click_disclaimer_if_present(self, page) -> None:
        """Real, confirmed via recon: Vale of Glamorgan's disclaimer
        button says "Accept & Continue" — a partial match, not an
        exact "Accept"/"Agree". Worcester has no disclaimer gate at
        all. Best-effort across several real candidate selectors."""
        if "Disclaimer" not in page.url:
            return
        for selector in [
            "input[value*='Accept' i]", "button:has-text('Accept')",
            "input[value='Agree']", "button:has-text('Agree')",
        ]:
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
        start_str = start.strftime("%d/%m/%Y")
        end_str = today.strftime("%d/%m/%Y")

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

            if self.needs_disclaimer:
                await self._click_disclaimer_if_present(page)
                if self.search_url not in page.url:
                    await page.goto(self.search_url, wait_until="domcontentloaded", timeout=45_000)

            await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
            await page.fill("#DateReceivedTo", end_str, timeout=5_000)

            planning_checkbox = page.locator("input[name='SearchPlanning'][type='checkbox']")
            if await planning_checkbox.count() > 0:
                if not await planning_checkbox.first.is_checked():
                    await planning_checkbox.first.check(timeout=5_000)

            form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
            search_scope = form_loc if await form_loc.count() > 0 else page

            clicked = False
            for sel in ["button:has-text('Search'):visible", "input[type='submit']:visible",
                        "button[type='submit']:visible"]:
                loc = search_scope.locator(sel)
                if await loc.count() > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                        await loc.last.click(timeout=5_000)
                    clicked = True
                    break

            if not clicked:
                self._log("⚠ Could not find/click a real submit button — stopping")
                await context.close()
                return []

            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            self._log(f"⚠ Search fill/submit failed: {type(e).__name__}: {e!r}")
            await context.close()
            return []

        self._log(f"Post-submit URL: {page.url}")

        html = await page.content()
        page1_apps = _parse_results_page(html, self.base_url, self.council_name)
        for a in page1_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
        self._log(f"Page 1: {len(page1_apps)} found (running total {len(all_apps)})")

        page_num = 2
        while page_num <= MAX_PAGES:
            if should_stop():
                self._log(f"⚠ Time budget reached at page {page_num}, stopping")
                break
            next_link = page.locator("a[aria-label='Next Page.']:visible")
            if await next_link.count() == 0:
                self._log(f"No visible 'Next' link — stopping at page {page_num - 1}")
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
                self._log(f"Page {page_num}: 0 NEW apps — stopping (likely genuine end of data)")
                break
            page_num += 1

        await context.close()
        return all_apps


async def process_council(portal: PlanningRegisterPortal, browser) -> int:
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
            "source": "planning_register_scraper",
        })

    if fallback_count:
        print(f"    [{portal.council_name}] Council centroid fallback for {fallback_count} apps")

    print(f"    [{portal.council_name}] Upserting {len(records)} records with council_id={cid}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"    [{portal.council_name}] ✓ Saved {len(records)}")
        await _supa_patch_council(cid, {
            "coverage_source": "planning_register_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })
    return len(records) if ok else 0


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind planning-register platform scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder (None) "
              "DB id in planning_register_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun planning_register_councils.py's INSERT_SQL in Supabase "
              "first, then replace each None above with the real id.")
        sys.exit(1)

    portals = [
        PlanningRegisterPortal(name, base_url, needs_disclaimer, COUNCIL_DB_IDS[name])
        for name, base_url, needs_disclaimer in PLANNING_REGISTER_COUNCILS
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
