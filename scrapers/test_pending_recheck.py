#!/usr/bin/env python3
"""
Quick real-code test for the decision-cadence fix (2026-07-20).

Calls the REAL IdoxPortal.scrape() with a mocked _scrape_month (no browser,
no network) to prove two things directly against the actual code path:

  1. Months belonging to pending-recheck applications get added to the
     scrape's month list (capped at MAX_RECHECK_MONTHS), even though
     they're outside the normal days_back window.
  2. A pending application whose submitted_date falls outside the 14-day
     cutoff is still kept in the final `recent` list, because its
     reference is in pending_refs — while a genuinely-new old application
     (not in pending_refs) still gets correctly filtered out.

Run: python3 scrapers/test_pending_recheck.py
"""
import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from idox_scraper import IdoxPortal


async def main():
    today = date.today()

    # Two months ago and 90 days ago — both outside a 14-day cutoff, so
    # only reachable at all if the recheck-month logic adds them.
    old_month_60 = (today - timedelta(days=60)).replace(day=1)
    old_month_90 = (today - timedelta(days=90)).replace(day=1)

    # Fake month-scrape results: one already-tracked pending app in each
    # old month, plus a genuinely NEW old application that should still be
    # filtered out (not in pending_refs).
    fake_apps_by_month = {
        today.replace(day=1): [
            {"reference": "26/00001/FUL", "submitted_date": today.isoformat(), "status": "pending"},
        ],
        old_month_60: [
            {"reference": "26/00777/FUL", "submitted_date": (today - timedelta(days=60)).isoformat(),
             "status": "approved"},  # this is the one that should now flow through
            {"reference": "26/00999/FUL", "submitted_date": (today - timedelta(days=61)).isoformat(),
             "status": "pending"},  # NOT in pending_refs — should still be filtered out
        ],
        old_month_90: [
            {"reference": "26/00333/FUL", "submitted_date": (today - timedelta(days=90)).isoformat(),
             "status": "refused"},
        ],
    }

    async def fake_scrape_month(self, page, for_month):
        return fake_apps_by_month.get(for_month, [])

    pending_recheck = [
        {"reference": "26/00777/FUL", "submitted_date": (today - timedelta(days=60)).isoformat()},
        {"reference": "26/00333/FUL", "submitted_date": (today - timedelta(days=90)).isoformat()},
    ]

    portal = IdoxPortal("Test Council", "https://example.gov.uk/planning", db_council_id=1)

    with patch.object(IdoxPortal, "_scrape_month", fake_scrape_month):
        # browser/context/page machinery is bypassed by patching _scrape_month,
        # but scrape() still opens a real context — mock the browser minimally.
        fake_browser = AsyncMock()
        fake_context = AsyncMock()
        fake_page = AsyncMock()
        fake_browser.new_context.return_value = fake_context
        fake_context.new_page.return_value = fake_page

        results = await portal.scrape(fake_browser, days_back=14, pending_recheck=pending_recheck)

    refs = {a["reference"] for a in results}
    print(f"Returned references: {sorted(refs)}")

    checks = [
        ("today's new app included", "26/00001/FUL" in refs),
        ("60-day-old PENDING-recheck app (now approved) included", "26/00777/FUL" in refs),
        ("90-day-old PENDING-recheck app (now refused) included", "26/00333/FUL" in refs),
        ("60-day-old app NOT in pending_refs correctly excluded", "26/00999/FUL" not in refs),
    ]

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
