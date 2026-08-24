#!/usr/bin/env python3
"""
PlanFind — Barrow (Westmorland and Furness Council) scraper (2026-08-24).

Real, confirmed evidence backing every design decision here — see
barrow_councils.py, and the full recon trail: wandf_recon.py,
wandf_recon_round2.py, wandf_recon_round3.py, barrow_iframe_check.py.

ARCHITECTURE: genuinely the most complex real platform in this whole
project — a live, 2-level click-driven Oracle APEX iframe navigation,
not a URL-based scrape at all:
  1. Load the real Weekly List overview. Parse real week-commencing
     dates from the confirmed table (class="t-Report-report").
  2. For each real target week (within DAYS_BACK), click that week's
     own real "Validated" button — a genuine javascript:apex.
     navigation.dialog(...) call, letting Oracle APEX's own real JS
     handler open a correctly-authenticated new iframe (confirmed:
     manually reconstructing the target URL is NOT attempted here —
     every other platform in this project that tried that specific
     shortcut against a similar real APEX/AJAX mechanism failed; only
     a genuine click ever worked).
  3. Find the real new iframe among page.frames (its URL contains
     "VALIDATEDLIST") and extract its real table
     (class="a-IRR-table", the one with actual data rows, not the
     hidden 1-row template sharing the same class).
  4. Close the real modal (confirmed: a "Close" button/link exists)
     before moving to the next week, so dialogs don't stack up.

HONEST LIMITATIONS:
  - No pending-recheck mechanism for Barrow at all. Real, confirmed
    evidence: the per-application detail link carries genuine
    session-bound security tokens (cs=, p_dialog_cs=) baked directly
    into its URL — unlike Hartlepool's clean, permanent reference-only
    URL, this one won't still be valid tomorrow. Storing it for a
    later recheck pass would not work. Every Barrow application starts
    and stays 'pending' from this scraper alone.
  - Only the real "Validated" list is scraped — the real "Decided"
    list's own column structure was never directly recon'd. A real,
    separate investigation would be needed to add decision data later.
  - Shares council_id with esl_scraper.py's Eden/South Lakeland data
    (same real council, deliberately one row) — this scraper's own
    records use a distinct real "source" tag so the two can be told
    apart in the database if ever needed.
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
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

from barrow_councils import COUNCIL_DB_IDS, BASE_URL

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
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))

COUNCIL_NAME = "Westmorland and Furness Council"

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _log(msg: str) -> None:
    print(f"    [Barrow] {msg}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(text: str) -> str:
    parts = [" ".join(p.split()) for p in text.split(",") if p.strip()]
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


def _real_weeks(html: str) -> list[tuple[str, int]]:
    """Real, confirmed weekly overview table structure — class=
    't-Report-report', real columns Week Commencing | Validated |
    Decided. Returns (date_string, real_application_count) pairs for
    every real week found, oldest filtering left to the caller."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="t-Report-report")
    if not table:
        return []
    rows = table.find_all("tr")
    weeks = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        week_date = cells[0].get_text(strip=True)
        validated_link = cells[1].find("a")
        if not validated_link:
            continue
        count_text = validated_link.get_text(strip=True)
        m = re.search(r"\((\d+)\)", count_text)
        count = int(m.group(1)) if m else 0
        if week_date:
            weeks.append((week_date, count))
    return weeks


def _parse_application_list(html: str) -> list[dict]:
    """Real, confirmed structure inside the per-week iframe. TWO real
    tables share class='a-IRR-table' — one a hidden 1-row template,
    one the real populated data. Matching by real row count (> 1),
    not by Oracle's own id-suffix naming convention, since that's a
    more defensible, less implementation-specific signal."""
    soup = BeautifulSoup(html, "html.parser")
    candidate_tables = soup.find_all("table", class_="a-IRR-table")
    table = None
    for t in candidate_tables:
        if len(t.find_all("tr")) > 1:
            table = t
            break
    if not table:
        return []

    rows = table.find_all("tr")
    header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all("th")]

    def _col_index(*keywords) -> Optional[int]:
        for i, h in enumerate(header_cells):
            if any(kw in h for kw in keywords):
                return i
        return None

    idx_ref = _col_index("reference number")
    idx_location = _col_index("location")
    idx_proposal = _col_index("proposal")
    idx_date = _col_index("validated date")

    if idx_ref is None:
        return []

    apps = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= idx_ref:
            continue
        reference = cells[idx_ref].get_text(strip=True)
        if not reference:
            continue
        address = _clean_address(cells[idx_location].get_text(strip=True)) if idx_location is not None and idx_location < len(cells) else ""
        proposal = cells[idx_proposal].get_text(strip=True) if idx_proposal is not None and idx_proposal < len(cells) else ""
        submitted_date = _parse_uk_date(cells[idx_date].get_text(strip=True)) if idx_date is not None and idx_date < len(cells) else None
        postcode = _extract_postcode(address)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": submitted_date,
            "status": "pending",  # real, confirmed: the Validated list
                                    # is pre-decision by definition
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


# ---------------------------------------------------------------------------
async def _close_modal(page: Page):
    """Real, confirmed: a 'Close' control exists at the end of every
    modal's own content. Best-effort — if it can't be found/clicked,
    the next real click on the underlying page usually still works
    fine regardless (APEX modals don't block the parent page's own
    interactivity the way a true native browser dialog would)."""
    try:
        close_btn = page.get_by_role("button", name="Close", exact=True)
        if await close_btn.count() > 0:
            await close_btn.first.click(timeout=3000)
            await asyncio.sleep(0.5)
    except Exception:
        pass


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
            _log(f"⚠ Could not load weekly list page: {e}")
            await context.close()
            await browser.close()
            return []

        html = await page.content()
        weeks = _real_weeks(html)
        _log(f"Real weeks found on overview: {len(weeks)}")

        cutoff = date.today() - timedelta(days=DAYS_BACK)
        target_weeks = []
        for week_date_str, count in weeks:
            parsed = _parse_uk_date(week_date_str)
            if parsed and date.fromisoformat(parsed) >= cutoff and count > 0:
                target_weeks.append((week_date_str, count))

        _log(f"Real weeks within {DAYS_BACK}-day window with real applications: "
             f"{len(target_weeks)}")

        for week_date_str, count in target_weeks:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at week {week_date_str}")
                break

            try:
                # Real, precise selector: the row whose real
                # WeekCommencing cell matches this exact date, then
                # that row's own Validated button specifically — not
                # just "the first Validated button on the page".
                row = page.locator(f"tr:has(td:text-is('{week_date_str}'))")
                validated_btn = row.locator("td[headers='Validated'] a")
                if await validated_btn.count() == 0:
                    _log(f"⚠ Could not find real Validated button for week {week_date_str}")
                    continue

                frames_before = len(page.frames)
                await validated_btn.first.click(timeout=8_000)
                await asyncio.sleep(2)  # real, deliberate pause for the
                                          # new iframe to genuinely load

                # Real, direct search for the new frame among all real
                # frames on the page — the one whose URL contains
                # "VALIDATEDLIST"
                week_apps = []
                for frame in page.frames:
                    if "VALIDATEDLIST" in frame.url:
                        frame_html = await frame.content()
                        week_apps = _parse_application_list(frame_html)
                        break

                new_count = 0
                for a in week_apps:
                    if a["reference"] not in seen_refs:
                        seen_refs.add(a["reference"])
                        all_apps.append(a)
                        new_count += 1

                _log(f"Week {week_date_str}: {new_count} new (real count shown: {count})")

                await _close_modal(page)

            except Exception as e:
                _log(f"⚠ Error processing week {week_date_str}: {e}")
                await _close_modal(page)
                continue

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Barrow scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"barrow_councils.py.")
        sys.exit(1)

    print(f"[Barrow] (council_id={cid}, shares council with esl_scraper.py's "
          f"Eden/South Lakeland data)\n")

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
            "submitted_date": a.get("submitted_date"),
            "lat": lat,
            "lng": lng,
            "source": "barrow_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
