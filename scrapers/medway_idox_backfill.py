#!/usr/bin/env python3
"""
Medway one-off history backfill (2026-10-05).

Medway's real register is an Idox portal (publicaccess1.medway.gov.uk),
confirmed by idox_url_probe.py. Once it is in idox_councils.py the nightly
batch fetches only the last 14 days, so Medway's page would start almost
empty. This pulls roughly six months of history in one go.

It runs the PRODUCTION save path (idox_scraper.process_council), so it
geocodes, de-duplicates, upserts in batches and updates the council row
exactly as a nightly run does — the only difference is the look-back.
Upserts are keyed on (council_id, reference), so re-running is safe and
never creates duplicates.

Uses the URL from idox_councils.py, so Medway must already be in that
file (the script stops with a clear message if it isn't).

Env: BACKFILL_DAYS (default 180), BACKFILL_BUDGET_MINUTES (default 45).
"""
import asyncio
import os
import sys
from collections import Counter

from playwright.async_api import async_playwright

import idox_scraper as scraper                      # production code, unchanged
from idox_councils import IDOX_COUNCILS

COUNCIL_NAME = "Medway Council"
DAYS = int(os.environ.get("BACKFILL_DAYS", "180"))
BUDGET = int(os.environ.get("BACKFILL_BUDGET_MINUTES", "45"))


def find_entry(name):
    """(url, extra_search_param) for the council, or None if not in the list."""
    for entry in IDOX_COUNCILS:
        if entry[0] == name:
            return entry[1], (entry[3] if len(entry) == 4 else "")
    return None


def pick_council_id(rows):
    """Exactly one councils row must match; anything else is refused, because
    a wrong or missing id would write applications under the wrong council."""
    if len(rows) != 1:
        raise ValueError(f"expected exactly one councils row named {COUNCIL_NAME!r}, found {len(rows)}")
    cid = rows[0].get("id")
    if not isinstance(cid, int) or cid <= 0:
        raise ValueError(f"unusable council id: {cid!r}")
    return cid


def summarise(rows) -> dict:
    return {
        "total": len(rows),
        "by_source": dict(Counter(r.get("source") or "unknown" for r in rows)),
        "by_month": dict(sorted(Counter((r.get("submitted_date") or "undated")[:7] for r in rows).items())),
        "by_status": dict(Counter(r.get("status") or "unknown" for r in rows)),
    }


async def fetch_saved_rows(cid):
    rows, page_size = [], 1000
    for page in range(10):                                # safety cap: 10,000 rows
        chunk = await scraper._supa_get(
            "planning_applications",
            select="source,submitted_date,status", council_id=f"eq.{cid}",
            order="id.asc", limit=str(page_size), offset=str(page * page_size))
        rows.extend(chunk)
        if len(chunk) < page_size:
            break
    return rows


async def main():
    if not scraper.SUPABASE_URL or not scraper.SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    entry = find_entry(COUNCIL_NAME)
    if entry is None:
        print(f"ERROR: {COUNCIL_NAME} is not in IDOX_COUNCILS. Deploy the updated "
              f"idox_councils.py first, then re-run this.")
        sys.exit(1)
    url, extra = entry

    try:
        cid = pick_council_id(await scraper._supa_get(
            "councils", select="id,name", name=f"eq.{COUNCIL_NAME}"))
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"{COUNCIL_NAME}: council_id={cid}\nPortal: {url}\n"
          f"Look-back: {DAYS} days, time budget {BUDGET} minutes\n")

    portal = scraper.IdoxPortal(COUNCIL_NAME, url, cid, extra_search_param=extra)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        result = await scraper.process_council(
            portal, browser, asyncio.Semaphore(1), days_back=DAYS,
            bulk_mode=False, budget_minutes=BUDGET)
        await browser.close()

    if result == "TIME_BUDGET_SKIP":
        print("\nStopped on the time budget before starting — raise BACKFILL_BUDGET_MINUTES.")
        sys.exit(1)
    print(f"\nprocess_council reported: {result} application(s) saved")

    summary = summarise(await fetch_saved_rows(cid))
    print(f"\n{'=' * 60}\nWhat Medway now has in the database\n{'=' * 60}")
    print(f"  total:     {summary['total']}")
    print(f"  by source: {summary['by_source']}")
    print(f"  by month:  {summary['by_month']}")
    print(f"  by status: {summary['by_status']}")
    print("\n  For comparison, the earlier probe found Aug 113 / Sep 159 received "
          "(its first 60 days only); older months should be of a similar size.")
    if summary["total"] == 0 or not result:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
