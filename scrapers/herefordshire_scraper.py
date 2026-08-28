#!/usr/bin/env python3
"""
PlanFind — Herefordshire Council scraper (2026-08-28).

Real, confirmed evidence backing every design decision — see
herefordshire_councils.py. Genuinely one of the simplest platforms in
this project once the right approach was found: pure, direct URL
construction handles both search and pagination, no Playwright
form-interaction needed at all beyond loading each constructed URL.

ARCHITECTURE: construct the real "Weekly list" search URL directly
with a genuine 30-day date-from/date-to range and status=all, parse
the real results table, increment the real &offset= parameter by 10
until the real confirmed total count is reached.

HONEST LIMITATIONS:
  - Real "Status" column is a genuine workflow stage ("Valid
    (Undecided)"), not a final decision outcome — defaults to
    'pending'. A real pending-recheck mechanism IS possible here (a
    genuine, permanent, reference-based detail URL exists), matching
    the same real recheck pattern already proven for the "Search/
    Advanced" and OcellaWeb families — real detail-page field labels
    for decision info were never directly recon'd, so the recheck
    logic uses a defensive keyword search, same discipline as before a
    detail page has ever been directly seen elsewhere in this project.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeout

from herefordshire_councils import COUNCIL_DB_IDS, BASE_URL

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
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "50"))

COUNCIL_NAME = "Herefordshire Council"

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _normalise_status(s: str) -> str:
    if not s:
        return "pending"
    d = s.lower()
    if any(x in d for x in ("approv", "grant", "permit", "allow", "no objection")):
        return "approved"
    if any(x in d for x in ("refus", "reject", "dismiss")):
        return "refused"
    if "withdraw" in d:
        return "withdrawn"
    return "pending"


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _build_search_url(date_from: date, date_to: date, offset: int) -> str:
    """Real, confirmed via herefordshire_wide_range_test.py: a plain,
    direct GET URL handles both the search itself and pagination (via
    the real &offset= parameter) — no clicking needed at all."""
    return (
        f"{BASE_URL}/planning-and-building-control/planning-search"
        f"?search-service=search&search-source=search&search-item="
        f"&date-to={date_to.isoformat()}&search-term="
        f"&date-from={date_from.isoformat()}&status=all"
        f"&weeklyParishSearch={quote('Weekly parish search')}"
        f"&offset={offset}"
    )


def _parse_results_table(html: str) -> tuple[list[dict], Optional[int]]:
    """Real, confirmed structure: a single <table>, real header row
    with <th>, columns Application number | Site address | Description
    | Type | Status | Comments by. Returns (apps, real_total_count)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")

    real_total = None
    m = re.search(r"of (\d+) for search", soup.get_text())
    if m:
        real_total = int(m.group(1))

    if not table:
        return [], real_total

    rows = table.find_all("tr")
    if len(rows) < 1:
        return [], real_total

    header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all("th")]

    def _col_index(*keywords) -> Optional[int]:
        for i, h in enumerate(header_cells):
            if any(kw in h for kw in keywords):
                return i
        return None

    idx_ref = _col_index("application number")
    idx_address = _col_index("site address")
    idx_desc = _col_index("description")
    idx_status = _col_index("status")

    if idx_ref is None:
        return [], real_total

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
            detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"

        address = cells[idx_address].get_text(" ", strip=True) if idx_address is not None and idx_address < len(cells) else ""
        description = cells[idx_desc].get_text(" ", strip=True) if idx_desc is not None and idx_desc < len(cells) else ""
        status_raw = cells[idx_status].get_text(strip=True) if idx_status is not None and idx_status < len(cells) else ""
        postcode = _extract_postcode(address)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": description,
            "status": _normalise_status(status_raw) if "undecided" not in status_raw.lower() else "pending",
            "council_url": detail_url,
        })

    return apps, real_total


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


async def scrape(browser: Browser) -> list[dict]:
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    today = date.today()
    start = today - timedelta(days=DAYS_BACK)

    offset = 0
    page_num = 1
    real_total = None

    while page_num <= MAX_PAGES:
        if should_stop():
            _log(f"⚠ Time budget reached, stopping at page {page_num}")
            break

        url = _build_search_url(start, today, offset)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Navigation error on page {page_num}: {e}")
            break

        if page_num == 1:
            try:
                accept_btn = page.get_by_text("Accept cookies", exact=True)
                if await accept_btn.count() > 0:
                    await accept_btn.first.click(timeout=5_000)
                    await asyncio.sleep(1)
            except Exception:
                pass

        html = await page.content()
        page_apps, total = _parse_results_table(html)
        if total is not None:
            real_total = total

        new_count = 0
        for a in page_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
                new_count += 1

        _log(f"Page {page_num} (offset={offset}): {new_count} new (running total "
             f"{len(all_apps)}" + (f" of {real_total} real total" if real_total else "") + ")")

        if not page_apps:
            break
        if real_total is not None and len(all_apps) >= real_total:
            break

        offset += 10
        page_num += 1
        await asyncio.sleep(1)  # real, deliberate small pause between
                                  # direct page requests

    await context.close()
    return all_apps


async def recheck_pending(browser: Browser, pending: list[dict]) -> list[dict]:
    """Real, confirmed permanent, reference-based detail URL — a
    genuine pending-recheck mechanism is possible here. Real detail-
    page field labels never actually recon'd — using a defensive
    keyword search, same discipline as before a detail page has ever
    been directly seen elsewhere in this project."""
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

        text = ""
        try:
            text = await page.locator("body").inner_text()
        except Exception:
            continue

        m = re.search(r"status\s*\n?\s*([A-Za-z ,.'()-]+)", text, re.I)
        if m:
            status_text = m.group(1).strip()
            if "undecided" not in status_text.lower():
                status = _normalise_status(status_text)
                if status != "pending":
                    updates.append({"reference": p["reference"], "status": status})

    await context.close()
    if updates:
        _log(f"Recheck: {len(updates)} of {len(pending)} previously-pending "
             f"application(s) now have a real decision")
    return updates


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Herefordshire scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"herefordshire_councils.py.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

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
            _log(f"Pending recheck: {len(pending)} applications (bounded to {RECHECK_LIMIT})")
    except Exception as e:
        _log(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        raw_apps = await scrape(browser)
        recheck_updates = await recheck_pending(browser, pending)

        await browser.close()

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
            "council_id": cid,
            "reference": a["reference"],
            "address": a["address"] or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "herefordshire_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    recheck_records = [{
        "council_id": cid,
        "reference": u["reference"],
        "status": u["status"],
    } for u in recheck_updates]

    saved_count = 0
    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        if await _supa_upsert(records):
            saved_count += len(records)

    if recheck_records:
        _log(f"Upserting {len(recheck_records)} recheck status updates with council_id={cid}")
        if await _supa_upsert(recheck_records):
            saved_count += len(recheck_records)

    if saved_count:
        _log(f"✓ Saved {saved_count}")
        await _supa_patch_council(cid, {
            "coverage_source": "herefordshire_weekly_search",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
