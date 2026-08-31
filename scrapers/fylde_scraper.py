#!/usr/bin/env python3
"""
PlanFind — Fylde Council scraper (2026-08-31).

Real, confirmed evidence backing every design decision — see
fylde_councils.py. Genuinely high confidence — unlike most of this
backlog batch, a real submission was actually captured and validated
before this was written.

ARCHITECTURE: Playwright accepts the disclaimer once, submits the real
date-range search, then paginates through the confirmed stable
/Search/ResultsPage/<page>?module=PLA URLs within the same browser
context (session cookie carried over automatically).
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

from fylde_councils import COUNCIL_DB_IDS, BASE_URL, SEARCH_URL

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

COUNCIL_NAME = "Fylde Council"

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


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_fylde_status(s: str) -> str:
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

    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


def _parse_results_page(html: str) -> list[dict]:
    """Real, confirmed structure: table class="table-striped tblResults".
    Two such tables exist on the page (Planning, Building Control) with
    IDENTICAL headers — only the /Planning/Display/ href prefix
    reliably distinguishes Planning rows."""
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    for table in soup.find_all("table", class_="tblResults"):
        rows = table.find_all("tr")
        for row in rows:
            link = row.find("a", href=re.compile(r"^/Planning/Display/"))
            if not link:
                continue  # not a Planning row (or it's the header row)

            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            reference = link.get_text(strip=True)
            location_raw = cells[1].get_text(strip=True)
            proposal = cells[2].get_text(strip=True)
            status_raw = cells[3].get_text(strip=True)

            postcode = _extract_postcode(location_raw)
            detail_url = urljoin(BASE_URL, link["href"])

            apps.append({
                "reference": reference,
                "address": location_raw,
                "postcode": postcode,
                "description": proposal,
                "status": _normalise_fylde_status(status_raw),
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

            if "Disclaimer" in page.url:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    await page.click("button:has-text('Agree'), input[value='Agree']")

            if "Search/Advanced" not in page.url:
                await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)

            await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
            await page.fill("#DateReceivedTo", end_str, timeout=5_000)

            submit = page.locator(
                "button:has-text('Search'), input[type='submit'], button[type='submit']"
            ).last
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await submit.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Search fill/submit failed: {type(e).__name__}: {e!r}")
            await context.close()
            await browser.close()
            return []

        html = await page.content()
        page_apps = _parse_results_page(html)
        for a in page_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
        _log(f"Page 1: {len(page_apps)} found (running total {len(all_apps)})")

        page_num = 2
        while page_num <= MAX_PAGES:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at page {page_num}")
                break
            try:
                await page.goto(
                    f"{BASE_URL}/Search/ResultsPage/{page_num}?module=PLA",
                    wait_until="domcontentloaded", timeout=30_000,
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
            except Exception as e:
                _log(f"⚠ Pagination request failed at page {page_num}: {e}")
                break

            html = await page.content()
            page_apps = _parse_results_page(html)
            if not page_apps:
                break

            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1
            _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)})")

            if new_count == 0:
                break
            page_num += 1
            await asyncio.sleep(0.5)

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Fylde scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"fylde_councils.py. Run the INSERT_SQL there, look up the "
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

        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "fylde_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "fylde_bespoke",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
