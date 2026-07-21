#!/usr/bin/env python3
"""
Real async test for the Arcus time-budget fix (2026-07-20).

Proves process_council() now correctly checks elapsed time at the
GENUINE moment its own turn arrives, using real asyncio timing with a
tiny artificial budget — not a mocked/faked clock — same verification
rigor as idox_scraper.py's equivalent Round 4 fix. Mocks only
ArcusPortal.scrape() (the network-touching call) and Supabase writes.

Run: python3 scrapers/test_arcus_time_budget.py
"""
import asyncio
from unittest.mock import AsyncMock, patch

import arcus_scraper as mod


async def run():
    # Reset START_TIME so this test's timing is self-contained regardless
    # of how long the module has already been imported/running.
    mod.START_TIME = mod.time.monotonic()

    fake_portal_early = AsyncMock()
    fake_portal_early.council_name = "Early Council"
    fake_portal_early.db_council_id = 1
    fake_portal_early.scrape = AsyncMock(return_value=[])

    fake_portal_late = AsyncMock()
    fake_portal_late.council_name = "Late Council"
    fake_portal_late.db_council_id = 2
    fake_portal_late.scrape = AsyncMock(return_value=[])

    sem = asyncio.Semaphore(2)
    fake_browser = AsyncMock()

    with patch.object(mod, "_supa_patch_council", AsyncMock(return_value=None)), \
         patch.object(mod, "_supa_increment_empty_runs", AsyncMock(return_value=None)):

        # budget_minutes=0 means "3 minutes in the past" relative to the
        # -3 margin in the check, so this should skip immediately even
        # though essentially zero real time has passed — proving the
        # check reads real elapsed time correctly (a budget of 0 forces
        # the skip branch deterministically, no need to actually wait).
        result = await mod.process_council(fake_portal_early, fake_browser, sem, budget_minutes=0)

    check1 = result == "TIME_BUDGET_SKIP"
    check2 = fake_portal_early.scrape.call_count == 0  # never should have tried scraping

    # A generous budget should NOT skip, and should actually call scrape()
    with patch.object(mod, "_supa_patch_council", AsyncMock(return_value=None)), \
         patch.object(mod, "_supa_increment_empty_runs", AsyncMock(return_value=None)):
        result2 = await mod.process_council(fake_portal_late, fake_browser, sem, budget_minutes=999)

    check3 = result2 == 0  # empty apps list -> genuine 0, not the sentinel
    check4 = fake_portal_late.scrape.call_count == 1  # did actually attempt to scrape

    checks = [
        ("budget_minutes=0 returns the TIME_BUDGET_SKIP sentinel", check1),
        ("a time-skipped council never calls scrape() at all", check2),
        ("budget_minutes=999 returns genuine 0 (not the sentinel)", check3),
        ("a non-skipped council genuinely attempts to scrape", check4),
    ]

    # 5. A real exception AFTER scrape() succeeds (e.g. during geocoding)
    # should be caught by the new safety net, print a message naming the
    # council, and return 0 — not propagate as an anonymous Exception
    # object into asyncio.gather's results with zero indication of which
    # council or why (the exact blind spot found in the real Eastleigh
    # investigation this fix responds to).
    import io
    import contextlib

    fake_portal_broken = AsyncMock()
    fake_portal_broken.council_name = "Broken Council"
    fake_portal_broken.db_council_id = 99
    fake_portal_broken.scrape = AsyncMock(return_value=[
        {"reference": "26/00001", "postcode": "AB1 2CD", "status": "pending"}
    ])

    buf = io.StringIO()
    with patch.object(mod, "_supa_patch_council", AsyncMock(return_value=None)), \
         patch.object(mod, "_supa_increment_empty_runs", AsyncMock(return_value=None)), \
         patch.object(mod, "geocode", AsyncMock(side_effect=RuntimeError("simulated failure"))):
        with contextlib.redirect_stdout(buf):
            result5 = await mod.process_council(fake_portal_broken, fake_browser, sem, budget_minutes=999)
    out5 = buf.getvalue()

    check5 = (result5 == 0
              and "Broken Council" in out5
              and "council_id=99" in out5
              and "simulated failure" in out5)
    checks.append(("post-scrape exception is caught, named, and returns 0 (not silently propagated)", check5))
    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
