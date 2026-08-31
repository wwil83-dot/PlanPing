#!/usr/bin/env python3
"""
PlanFind — Redcar and Cleveland Borough Council scraper (2026-08-31).

Real, confirmed evidence backing the search-form design — see
redcar_cleveland_councils.py. Results-page structure is UNCONFIRMED —
see that file's HONEST LIMITATION note. This scraper uses generic,
defensive result-row detection and logs a full diagnostic dump on the
first run so the real structure can be confirmed and, if needed,
tightened up afterward — same pattern as this project's other brand-
new, never-yet-run platforms.

DELIBERATELY manual-trigger only until a confirmed clean run.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
import httpx
from bs4 import BeautifulSoup

from redcar_cleveland_councils import COUNCIL_DB_IDS, SEARCH_URL

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

COUNCIL_NAME = "Redcar and Cleveland Borough Council"
BASE_URL = "https://planning.redcar-cleveland.gov.uk"

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_results_page_generic(html: str) -> list[dict]:
    """UNCONFIRMED structure — generic defensive parsing. Tries a
    standard results <table> first; falls back to any repeated block
    containing something that looks like a reference number."""
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            row_text = " | ".join(c.get_text(strip=True) for c in cells)
            ref_match = re.search(r"\bR/\d{4}/\d+/[A-Z]{2,4}\b", row_text)
            if not ref_match:
                continue
            link = row.find("a")
            detail_url = urljoin(BASE_URL, link.get("href")) if link and link.get("href") else None
            apps.append({
                "reference": ref_match.group(0),
                "raw_row_text": row_text,
                "council_url": detail_url,
            })

    if not apps:
        # Fallback: look for reference patterns anywhere with surrounding links
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True)
            ref_match = re.search(r"\bR/\d{4}/\d+/[A-Z]{2,4}\b", text)
            if ref_match:
                apps.append({
                    "reference": ref_match.group(0),
                    "raw_row_text": text,
                    "council_url": urljoin(BASE_URL, link["href"]),
                })

    # REAL FIX (2026-08-31) — first live run hit a real Postgres error:
    # "ON CONFLICT DO UPDATE command cannot affect row a second time".
    # The generic table scan can match the same reference more than
    # once (e.g. a details/consultation sub-table repeating the same
    # case). Dedupe by reference, keeping the first (richest) match.
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


async def scrape() -> list[dict]:
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
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
            await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
            await page.fill("#DateReceivedTo", end_str, timeout=5_000)
            submit = page.locator("input[type='submit']").last
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await submit.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Search fill/submit failed: {e}")
            await context.close()
            await browser.close()
            return []

        _log(f"Post-submit URL: {page.url}")
        html = await page.content()
        apps = _parse_results_page_generic(html)

        _log(f"Parsed {len(apps)} apps with generic detection")
        if not apps:
            body_text = (await page.locator("body").inner_text())[:1500]
            _log(f"⚠ RESULTS DIAGNOSTIC: 0 apps parsed. Real body text "
                 f"(first 1500 chars): {body_text!r}")
        else:
            _log(f"⚠ RESULTS DIAGNOSTIC (unconfirmed structure): first "
                 f"parsed row raw text: {apps[0].get('raw_row_text', '')!r}")

        await context.close()
        await browser.close()

    return apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Redcar and Cleveland scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"redcar_cleveland_councils.py. Run the INSERT_SQL there, "
              f"look up the real id, and fill it in before running this "
              f"scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results parsed — see RESULTS DIAGNOSTIC above. "
              "Nothing to save; real HTML inspection needed before retry.")
        return

    records = []
    for a in raw_apps:
        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "description": a.get("raw_row_text", "")[:500] or None,
            "status": "pending",
            "council_url": a.get("council_url"),
            "source": "redcar_cleveland_scraper",
        })

    _log(f"Upserting {len(records)} records with council_id={cid} "
         f"(minimal fields — see diagnostic notes above)")
    ok = await _supa_upsert(records)
    if ok:
        _log(f"✓ Saved {len(records)}")
        await _supa_patch_council(cid, {
            "coverage_source": "redcar_cleveland_bespoke",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
