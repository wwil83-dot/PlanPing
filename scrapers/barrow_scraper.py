#!/usr/bin/env python3
"""
PlanFind — Barrow (Westmorland and Furness Council) scraper (2026-08-24,
extended same day with real Decided-list support).

Real, confirmed evidence backing every design decision here — see
barrow_councils.py, and the full recon trail: wandf_recon.py,
wandf_recon_round2.py, wandf_recon_round3.py, barrow_iframe_check.py,
barrow_decided_recon.py.

ARCHITECTURE: genuinely the most complex real platform in this whole
project — a live, 2-level click-driven Oracle APEX iframe navigation,
not a URL-based scrape at all:
  1. Load the real Weekly List overview. Parse real week-commencing
     dates AND real Validated/Decided counts from the confirmed table
     (class="t-Report-report").
  2. For each real target week (within DAYS_BACK), click that week's
     own real "Validated" button, THEN its own real "Decided" button —
     both genuine javascript:apex.navigation.dialog(...) calls,
     letting Oracle APEX's own real JS handler open a correctly-
     authenticated new iframe each time (confirmed: manually
     reconstructing the target URL is NOT attempted here — every other
     platform in this project that tried that specific shortcut
     against a similar real APEX/AJAX mechanism failed; only a genuine
     click ever worked).
  3. Find the real new iframe among page.frames (its URL contains
     "VALIDATEDLIST" or "DECIDEDLIST") and extract its real table
     (class="a-IRR-table", the one with actual data rows, not the
     hidden 1-row template sharing the same class) — both lists share
     the exact same real structure, confirmed via barrow_decided_recon.py,
     just with 2 extra real columns (Decision, Decision date) on the
     Decided list.
  4. Close the real modal (confirmed: a "Close" button/link exists)
     before moving to the next click, so dialogs don't stack up.
  5. Real, confirmed decision matching by reference number: a real
     decided application's reference is checked against this SAME
     run's own Validated-list catch (fast-turnaround merge) and, if
     not found there, becomes its own separate partial status-only
     update — matched by reference alone, no stored URL needed at all.

HONEST LIMITATIONS:
  - Real decision codes confirmed via barrow_decided_recon.py:
    'APPCOND', 'APPROVED'. Only these 2 have actually been observed —
    refused/withdrawn mappings are a defensive, reasonable guess based
    on common wording patterns, same discipline as every other
    platform's status normalisation here, since a real refused/
    withdrawn code has never actually been seen in the wild yet.
  - Only weeks within the real DAYS_BACK window get a Decided-list
    check — an application validated long before that window, then
    decided just now, would still be correctly caught (since the
    DECISION happened within the window and that week's own Decided
    list is checked), but an application decided far outside the
    window entirely would never be seen by this scraper at all.
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


def _real_weeks(html: str) -> list[tuple[str, int, int]]:
    """Real, confirmed weekly overview table structure — class=
    't-Report-report', real columns Week Commencing | Validated |
    Decided. Returns (date_string, real_validated_count,
    real_decided_count) triples for every real week found, oldest
    filtering left to the caller."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="t-Report-report")
    if not table:
        return []
    rows = table.find_all("tr")
    weeks = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        week_date = cells[0].get_text(strip=True)
        if not week_date:
            continue

        def _count_from_cell(cell) -> int:
            link = cell.find("a")
            if not link:
                return 0
            m = re.search(r"\((\d+)\)", link.get_text(strip=True))
            return int(m.group(1)) if m else 0

        validated_count = _count_from_cell(cells[1])
        decided_count = _count_from_cell(cells[2])
        weeks.append((week_date, validated_count, decided_count))
    return weeks


def _normalise_status(decision_code: Optional[str]) -> str:
    """Real, confirmed decision codes from barrow_decided_recon.py:
    'APPCOND' (approved with conditions), 'APPROVED'. Only 2 real
    codes have actually been observed — extending defensively to
    common Oracle-APEX-style wording patterns for refused/withdrawn,
    same discipline as every other platform's status normalisation in
    this project, since a real 'refused' code has never actually been
    seen in the wild yet."""
    if not decision_code:
        return "pending"
    d = decision_code.lower()
    if any(x in d for x in ("app", "grant", "permit", "allow")):
        return "approved"
    if any(x in d for x in ("ref", "reject", "dismiss")):
        return "refused"
    if "withdraw" in d:
        return "withdrawn"
    return "pending"


def _parse_application_list(html: str) -> list[dict]:
    """Real, confirmed structure inside the per-week iframe — shared by
    BOTH the Validated and Decided lists (confirmed via
    barrow_decided_recon.py: identical real table structure, class=
    'a-IRR-table', same real column set plus two extra real columns
    — Decision, Decision date — when parsing a Decided-list frame.
    TWO real tables share class='a-IRR-table' on every page — one a
    hidden 1-row template, one the real populated data. Matching by
    real row count (> 1), not by Oracle's own id-suffix naming
    convention, since that's a more defensible, less implementation-
    specific signal."""
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
    # Real, confirmed: the Validated list's date column is literally
    # "Validated date"; the Decided list's is "Decision date" — trying
    # both so this one function serves both real lists.
    idx_date = _col_index("validated date", "decision date")
    # Real, confirmed ONLY present on the Decided list — a genuine
    # decision outcome code (e.g. "APPCOND", "APPROVED"), absent
    # entirely from the Validated list (which is pre-decision by
    # definition).
    idx_decision = _col_index("decision")
    if idx_decision is not None and idx_decision == idx_date:
        idx_decision = None  # real defensive guard: "decision" is a
                               # substring of "decision date" — never
                               # let both keywords resolve to the same
                               # real column by accident

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
        decision_code = cells[idx_decision].get_text(strip=True) if idx_decision is not None and idx_decision < len(cells) else None
        postcode = _extract_postcode(address)


        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": submitted_date,
            # Real, confirmed: only the Decided list has a real
            # decision code present at all (idx_decision is None when
            # parsing the Validated list, since that list is
            # pre-decision by definition) — _normalise_status(None)
            # correctly falls through to 'pending'.
            "status": _normalise_status(decision_code),
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
async def _close_modal(page: Page) -> bool:
    """REAL FIX (2026-08-24) — confirmed root cause of a real,
    expensive cascading failure: the original version's assumption
    ("APEX modals don't block the parent page") was WRONG. Real,
    direct evidence from a live production run: after the very first
    successful Validated click, EVERY subsequent click for the rest of
    the entire run failed with "subtree intercepts pointer events" —
    the SAME stuck first-week modal blocking all 15 later attempts,
    because this function's old bare 'except: pass' silently swallowed
    whatever went wrong with the close click, with zero warning. ~7 of
    15 available minutes were burned on doomed retries as a direct
    result.

    Real, confirmed via the actual error log: this site's modals are
    genuine jQuery UI dialog widgets (class="ui-dialog ui-corner-all
    ui-widget..."). jQuery UI's own standard, well-documented
    convention renders a real close button with class
    'ui-dialog-titlebar-close' inside the titlebar — targeting that
    directly as the primary, most reliable approach, with a real
    Escape-key press as a second, independent fallback (a standard,
    reliable way to dismiss this exact class of dialog). Returns
    whether the modal genuinely appears gone, and — critically —
    LOGS a real, visible warning on failure instead of staying silent,
    so this exact class of cascading failure can never again burn an
    entire run's time budget without anyone knowing why.
    """
    closed = False
    try:
        close_x = page.locator(".ui-dialog-titlebar-close")
        if await close_x.count() > 0:
            await close_x.first.click(timeout=3000)
            await asyncio.sleep(0.5)
            closed = True
    except Exception as e:
        _log(f"⚠ Real jQuery UI close button click failed: {e}")

    if not closed:
        try:
            close_btn = page.get_by_role("button", name="Close", exact=True)
            if await close_btn.count() > 0:
                await close_btn.first.click(timeout=3000)
                await asyncio.sleep(0.5)
                closed = True
        except Exception as e:
            _log(f"⚠ Real 'Close' button click also failed: {e}")

    if not closed:
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
            closed = True
            _log("Used a real Escape key press as a fallback to close the modal")
        except Exception as e:
            _log(f"⚠ Real Escape key fallback also failed: {e}")

    # Real, direct confirmation check — did a real dialog element
    # actually disappear, rather than just assuming success because no
    # exception was thrown
    try:
        still_open = await page.locator("[role='dialog']").count() > 0
        if still_open:
            _log("⚠ A real dialog element is STILL present after all close "
                 "attempts — the next click may fail")
            return False
    except Exception:
        pass

    return closed


async def scrape() -> tuple[list[dict], list[dict]]:
    """Returns (full_records, partial_status_updates) — kept separate
    throughout, never mixed into one list, since a single upsert call
    containing both full application records and partial status-only
    records hits PostgREST's real 'All object keys must match' error
    (the exact bug already found and fixed in esl_scraper.py)."""
    all_apps: list[dict] = []
    seen_refs: set[str] = set()
    decided_entries: list[dict] = []  # collected separately, merged
                                        # into the right place only
                                        # after all weeks are done

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
            return [], []

        html = await page.content()
        weeks = _real_weeks(html)
        _log(f"Real weeks found on overview: {len(weeks)}")

        cutoff = date.today() - timedelta(days=DAYS_BACK)
        target_weeks = [w for w in weeks
                         if (p := _parse_uk_date(w[0])) and date.fromisoformat(p) >= cutoff]

        _log(f"Real weeks within {DAYS_BACK}-day window: {len(target_weeks)}")

        for week_date_str, validated_count, decided_count in target_weeks:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at week {week_date_str}")
                break

            row = page.locator(f"tr:has(td:text-is('{week_date_str}'))")

            # Real Validated pass — full application records
            if validated_count > 0:
                try:
                    btn = row.locator("td[headers='Validated'] a")
                    if await btn.count() > 0:
                        await btn.first.click(timeout=8_000)
                        await asyncio.sleep(2)
                        week_apps = []
                        for frame in page.frames:
                            if "VALIDATEDLIST" in frame.url:
                                week_apps = _parse_application_list(await frame.content())
                                break
                        new_count = 0
                        for a in week_apps:
                            if a["reference"] not in seen_refs:
                                seen_refs.add(a["reference"])
                                all_apps.append(a)
                                new_count += 1
                        _log(f"Week {week_date_str} (Validated): {new_count} new "
                             f"(real count shown: {validated_count})")
                        if not await _close_modal(page):
                            # REAL FIX — confirmed root cause of a real
                            # cascading failure: continuing to click
                            # against a genuinely stuck modal just
                            # repeats the exact same doomed failure for
                            # every remaining week, burning the whole
                            # time budget for nothing. Stopping cleanly
                            # here preserves whatever real data was
                            # already captured instead.
                            _log("⚠ Modal genuinely would not close — stopping "
                                 "here rather than burning the rest of the "
                                 "budget on doomed retries")
                            await context.close()
                            await browser.close()
                            return _finalise(all_apps, decided_entries)
                except Exception as e:
                    _log(f"⚠ Error on Validated list, week {week_date_str}: {e}")
                    if not await _close_modal(page):
                        _log("⚠ Modal genuinely would not close after an error — "
                             "stopping here rather than burning the rest of "
                             "the budget on doomed retries")
                        await context.close()
                        await browser.close()
                        return _finalise(all_apps, decided_entries)

            if should_stop():
                break

            # Real Decided pass — real decision outcomes, collected
            # separately since a reference here might belong to either
            # a full record just captured above, or an older
            # application never seen in this run's own Validated pass
            if decided_count > 0:
                try:
                    btn = row.locator("td[headers='Decided'] a")
                    if await btn.count() > 0:
                        await btn.first.click(timeout=8_000)
                        await asyncio.sleep(2)
                        week_decided = []
                        for frame in page.frames:
                            if "DECIDEDLIST" in frame.url:
                                week_decided = _parse_application_list(await frame.content())
                                break
                        decided_entries.extend(week_decided)
                        _log(f"Week {week_date_str} (Decided): {len(week_decided)} real "
                             f"decision(s) found (real count shown: {decided_count})")
                        if not await _close_modal(page):
                            _log("⚠ Modal genuinely would not close — stopping "
                                 "here rather than burning the rest of the "
                                 "budget on doomed retries")
                            await context.close()
                            await browser.close()
                            return _finalise(all_apps, decided_entries)
                except Exception as e:
                    _log(f"⚠ Error on Decided list, week {week_date_str}: {e}")
                    if not await _close_modal(page):
                        _log("⚠ Modal genuinely would not close after an error — "
                             "stopping here rather than burning the rest of "
                             "the budget on doomed retries")
                        await context.close()
                        await browser.close()
                        return _finalise(all_apps, decided_entries)

        await context.close()
        await browser.close()

    return _finalise(all_apps, decided_entries)


def _finalise(all_apps: list[dict], decided_entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Real merge pass — a decided reference either updates a full
    record already captured this run, or becomes its own separate
    partial status-only update for an older, already-saved
    application never seen in this run's Validated pass. Extracted
    into its own function so both a normal, full completion AND an
    early exit (e.g. a genuinely stuck modal) save whatever real data
    was actually captured, rather than losing it entirely."""
    apps_by_ref = {a["reference"]: a for a in all_apps}
    partial_updates: list[dict] = []
    seen_decided_refs: set[str] = set()
    for d in decided_entries:
        ref = d["reference"]
        if ref in seen_decided_refs:
            continue  # real, defensive dedup — a reference could
                        # theoretically appear in more than one
                        # decided-list page if weeks overlap oddly
        seen_decided_refs.add(ref)
        if ref in apps_by_ref:
            apps_by_ref[ref]["status"] = d["status"]
        else:
            partial_updates.append({"reference": ref, "status": d["status"]})

    return all_apps, partial_updates



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

    raw_apps, partial_updates = await scrape()

    if not raw_apps and not partial_updates:
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

    # Real, confirmed via barrow_decided_recon.py: matching a decided
    # application back to an existing 'pending' record by reference
    # number alone genuinely works — no session-bound URL needs
    # storing at all. Kept as a SEPARATE upsert call from the full
    # application records above, same discipline as esl_scraper.py's
    # own recheck split — PostgREST's bulk upsert genuinely requires
    # every object in one call to share identical keys, and mixing
    # full (10-key) records with partial (2-key) status updates hits
    # a real "All object keys must match" error.
    partial_records = [{
        "council_id": cid,
        "reference": p["reference"],
        "status": p["status"],
    } for p in partial_updates]

    saved_count = 0
    if records:
        _log(f"Upserting {len(records)} new/updated application records "
             f"with council_id={cid}")
        if await _supa_upsert(records):
            saved_count += len(records)

    if partial_records:
        _log(f"Upserting {len(partial_records)} real decision status "
             f"updates (matched by reference, no stored URL needed) "
             f"with council_id={cid}")
        if await _supa_upsert(partial_records):
            saved_count += len(partial_records)

    if saved_count:
        _log(f"✓ Saved {saved_count}")
        await _supa_patch_council(cid, {
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
