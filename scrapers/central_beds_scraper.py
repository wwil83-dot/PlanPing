#!/usr/bin/env python3
"""
PlanFind — Central Bedfordshire Council (AcolNet) scraper (2026-08-30).

Real, confirmed evidence backing every design decision — see
central_beds_councils.py.

ARCHITECTURE: fill 2 real date fields (regdate1/regdate2, DD/MM/YYYY),
click the real Search button, parse the real per-application
"results-table" blocks, click through real session-bound "Next"
pagination links directly (cannot be constructed from a formula, must
be live-clicked).

HONEST LIMITATIONS:
  - Decision-field text for a genuinely DECIDED application was never
    observed during recon (every real result seen was still pending) —
    _normalise_central_beds_status() is a best-effort substring
    normaliser with diagnostic logging for anything unrecognised, same
    honest-gap pattern as ni_scraper.py.
  - No pending-recheck mechanism yet. Every application starts and
    stays whatever status it had at scrape time.
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

from central_beds_councils import COUNCIL_DB_IDS, BASE_URL, DETAIL_BASE

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
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "60"))

COUNCIL_NAME = "Central Bedfordshire Council"

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


def _parse_central_beds_date(text: str) -> Optional[str]:
    """Real, confirmed markup splits the date across whitespace-heavy
    text nodes (e.g. '28\\n\\n / \\n08\\n\\n / \\n2026'). Strips all
    whitespace before parsing DD/MM/YYYY."""
    if not text:
        return None
    cleaned = re.sub(r"\s+", "", text)
    try:
        return datetime.strptime(cleaned, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_central_beds_status(decision_text: str) -> str:
    """HONEST LIMITATION: only ever seen 'This case has not yet been
    decided' during recon — no real decided-application text observed.
    Best-effort substring match with diagnostic logging, same pattern
    as ni_scraper.py's _normalise_ni_status()."""
    if not decision_text:
        return "pending"
    key = decision_text.lower()
    if "not yet been decided" in key or "not been decided" in key:
        return "pending"
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"

    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised decision text {decision_text!r} — filed as 'pending'")
    return "pending"


def _field_text(block, label: str) -> str:
    """Given one results-table block (a BeautifulSoup <table> tag),
    find the <td> whose sibling <th> text starts with `label`."""
    for th in block.find_all("th"):
        if th.get_text(strip=True).lower().startswith(label.lower()):
            td = th.find_next_sibling("td")
            if td:
                return td.get_text(" ", strip=True)
    return ""


def _parse_results_page(html: str) -> tuple[list[dict], Optional[int], Optional[int]]:
    """Returns (apps, shown_so_far, real_total). Real, confirmed
    structure: one <table class="results-table"> per application."""
    soup = BeautifulSoup(html, "html.parser")

    body_text = soup.get_text()
    total_match = re.search(r"of\s+(\d+)\s+Results", body_text)
    real_total = int(total_match.group(1)) if total_match else None

    blocks = soup.find_all("table", class_="results-table")
    apps = []
    for block in blocks:
        ref_th = block.find("th", class_="casenumber")
        if not ref_th:
            continue
        ref_td = ref_th.find_next_sibling("td")
        if not ref_td:
            continue
        link = ref_td.find("a")
        if not link:
            continue

        reference = link.get_text(strip=True).replace("(click for more details)", "").strip()
        if not reference:
            continue

        href = link.get("href", "")
        detail_url = urljoin(DETAIL_BASE, href) if href else None

        reg_date = _parse_central_beds_date(_field_text(block, "Registration Date"))
        location = _field_text(block, "Location")
        parish = _field_text(block, "Parish Name")
        statutory_class = _field_text(block, "Statutory Class")
        proposal = _field_text(block, "Proposal")
        decision_text = _field_text(block, "Decision")

        postcode = _extract_postcode(location)
        address = ", ".join(p for p in (location, parish) if p)

        apps.append({
            "reference": reference,
            "submitted_date": reg_date,
            "address": address or None,
            "postcode": postcode,
            "description": proposal or None,
            "application_type": statutory_class or None,
            "status": _normalise_central_beds_status(decision_text),
            "council_url": detail_url,
        })

    return apps, len(apps), real_total


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

    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        _log(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            _log(f"⚠ Could not load search page: {e}")
            await context.close()
            await browser.close()
            return []

        try:
            await page.fill("#regdate1", start_str, timeout=5_000)
            await page.fill("#regdate2", end_str, timeout=5_000)
            search_buttons = page.locator(
                "button:has-text('Search'), "
                "input[type='submit'][value='Search'], "
                "input[type='button'][value='Search']"
            )
            btn_count = await search_buttons.count()
            if btn_count == 0:
                search_buttons = page.get_by_text("Search", exact=True)
                btn_count = await search_buttons.count()
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await search_buttons.nth(max(btn_count - 1, 0)).click()
        except Exception as e:
            _log(f"⚠ Could not fill/submit search: {e}")
            await context.close()
            await browser.close()
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        page_num = 1
        real_total = None

        while page_num <= MAX_PAGES:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at page {page_num}")
                break

            html = await page.content()
            page_apps, _, real_total = _parse_results_page(html)

            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1

            _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)}"
                 + (f" of {real_total} real total" if real_total else "") + ")")

            if not page_apps:
                break
            if real_total is not None and len(all_apps) >= real_total:
                break

            try:
                next_link = page.locator("a:has-text('Next')")
                if await next_link.count() == 0:
                    break
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    await next_link.first.click(timeout=5_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1)
                page_num += 1
            except Exception as e:
                _log(f"⚠ Could not click Next at page {page_num}: {e}")
                break

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Central Bedfordshire scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"central_beds_councils.py. Run the INSERT_SQL there, look up "
              f"the real id, and fill it in before running this scraper.")
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
            "submitted_date": a.get("submitted_date"),
            "address": a.get("address"),
            "postcode": a.get("postcode"),
            "description": a.get("description"),
            "application_type": a.get("application_type"),
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "central_beds_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "centralbeds_acolnet",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
