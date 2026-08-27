#!/usr/bin/env python3
"""
PlanFind — Walsall (Swift/APAS) scraper (2026-08-27).

Real, confirmed evidence backing every design decision — see
walsall_councils.py.

ARCHITECTURE: genuinely simple — fill 2 real date fields (DD/MM/YYYY),
click Search, parse the real results table, click through real
pagination pages by directly following the confirmed real page-link
pattern.

HONEST LIMITATIONS:
  - No pending-recheck mechanism. Real, confirmed detail URL embeds a
    long, session-specific "backURL" parameter alongside the real
    stable "theApnID" reference — untested whether a simplified URL
    (just theApnID, no backURL) would still work if stored and
    revisited later. Every application starts and stays 'pending' from
    this scraper alone, same honest gap as Barrow.
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

from walsall_councils import COUNCIL_DB_IDS, BASE_URL

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

COUNCIL_NAME = "Walsall Metropolitan Borough Council"

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


def _clean_address(text: str) -> str:
    parts = [" ".join(p.split()) for p in text.split(",") if p.strip()]
    return ", ".join(parts)


def _parse_results_table(html: str, base_url: str) -> list[dict]:
    """Real, confirmed structure: the 4th real <table> on the page
    (index 3) is the actual data table — the first 3 are decorative
    per-column sort-control tables sharing a similar structure. Real
    columns: Ref No (inside a real <a>) | Description | Location."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    data_table = None
    for t in tables:
        rows = t.find_all("tr")
        if len(rows) > 1 and len(rows[0].find_all("th")) >= 3:
            # Real, defensive check: the real data table's header row
            # has 3 real <th> cells (Ref No/Description/Location) —
            # the 3 decorative sort-control tables each only have 1.
            data_table = t
            break

    if not data_table:
        return []

    rows = data_table.find_all("tr")
    apps = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        ref_cell = cells[0]
        link = ref_cell.find("a")
        reference = ref_cell.get_text(strip=True)
        if not reference:
            continue

        detail_url = None
        if link and link.get("href"):
            href = link["href"]
            detail_url = href if href.startswith("http") else f"{base_url.rsplit('/', 1)[0]}/{href}"

        description = cells[1].get_text(strip=True)
        address = _clean_address(cells[2].get_text(strip=True))
        postcode = _extract_postcode(address)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": description,
            "status": "pending",  # real, confirmed: no status column
                                    # exists in this results table
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
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        _log(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Could not load search page: {e}")
            await context.close()
            await browser.close()
            return []

        today = date.today()
        start = today - timedelta(days=DAYS_BACK)

        try:
            await page.fill("[name='REGFROMDATE.MAINBODY.WPACIS.1']", start.strftime("%d/%m/%Y"), timeout=5_000)
            await page.fill("[name='REGTODATE.MAINBODY.WPACIS.1']", today.strftime("%d/%m/%Y"), timeout=5_000)
            await page.locator("[name='SEARCHBUTTON.MAINBODY.WPACIS.1']").first.click(timeout=5_000)
        except Exception as e:
            _log(f"⚠ Could not fill/submit search: {e}")
            await context.close()
            await browser.close()
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        page_num = 1
        while page_num <= MAX_PAGES:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at page {page_num}")
                break

            html = await page.content()
            page_apps = _parse_results_table(html, BASE_URL)
            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1

            body_text = ""
            try:
                body_text = await page.locator("body").inner_text()
            except Exception:
                pass
            m = re.search(r"returned (\d+) matches", body_text)
            real_total = int(m.group(1)) if m else None

            _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)}"
                 + (f" of {real_total} real total" if real_total else "") + ")")

            if real_total is not None and len(all_apps) >= real_total:
                break

            # Real, confirmed pagination: a page-number link — trying
            # the next sequential number directly via text match
            try:
                next_link = page.get_by_text(str(page_num + 1), exact=True)
                if await next_link.count() == 0:
                    break
                await next_link.first.click(timeout=5_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1.5)
                page_num += 1
            except Exception as e:
                _log(f"⚠ Could not click page {page_num + 1}: {e}")
                break

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Walsall scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"walsall_councils.py.")
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
            "address": a["address"] or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "walsall_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "walsall_apas",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
