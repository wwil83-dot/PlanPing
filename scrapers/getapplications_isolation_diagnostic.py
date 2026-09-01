#!/usr/bin/env python3
"""
PlanFind — getApplications diagnostic: isolation test (2026-09-01).

Real evidence to explain: the full 12-council run saved Liverpool
cleanly (114 apps) then got HTTP 405 "Human Verification" on EVERY
subsequent council — including Warrington, Newcastle, and Blackburn
with Darwen, all three previously confirmed working for weeks. That
pattern (first council clean, everything after it blocked) is
consistent with a real, plausible mechanism: if this shared hosting
vendor's bot detection operates at the PLATFORM level (not per-tenant
— see getapplications_councils.py's own citation that Warrington's
system "resembles the one used by Liverpool"), then one IP/browser
session rapidly visiting MANY DIFFERENT council instances of the same
shared platform in quick succession is a classic distributed-scraping
fingerprint that centralized, cross-tenant security would specifically
watch for — distinct from any single site's own per-tenant rate limit.
Adding 7 new councils to this run (previously 4-5) may have crossed
that threshold.

This reuses the REAL, already-correct scraper logic directly (imports
GetApplicationsPortal + process_council unchanged) rather than
reimplementing anything, and runs two isolated tests:

  TEST A: Liverpool + Warrington together, nothing else — matches the
          original known-good pair size. If Warrington succeeds here,
          that's strong evidence FOR the cross-domain-volume theory.

  TEST B: Warrington ALONE, no Liverpool at all — rules out "session
          tainted by visiting Liverpool first" as a confound. If
          Warrington succeeds completely alone, that further isolates
          the trigger to "how many DIFFERENT council domains visited
          in one session," not something wrong with Warrington itself.

Each test uses its own fresh browser instance (not sharing state
between tests), matching a genuinely clean, independent run.
"""
import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from playwright.async_api import async_playwright

from getapplications_scraper import GetApplicationsPortal, process_council, BROWSER_ARGS
from getapplications_councils import COUNCIL_DB_IDS, GETAPPLICATIONS_COUNCILS

WEEKS_BACK = 2

# Real base URLs, taken directly from the existing config — not
# retyped/guessed.
_BASE_URLS = dict(GETAPPLICATIONS_COUNCILS)


async def run_test(label: str, council_names: list[str]):
    print(f"\n{'=' * 70}")
    print(f"TEST: {label}")
    print(f"Councils: {council_names}")
    print("=" * 70)

    portals = [
        GetApplicationsPortal(name, _BASE_URLS[name], COUNCIL_DB_IDS[name])
        for name in council_names
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched fresh for this test: {browser.version}")

        sem = asyncio.Semaphore(1)  # matches real production CONCURRENCY=1
        results = await asyncio.gather(
            *[process_council(p, browser, sem, WEEKS_BACK, pending_recheck=None)
              for p in portals],
            return_exceptions=True,
        )

        await browser.close()

    for name, result in zip(council_names, results):
        if isinstance(result, Exception):
            print(f"  {name}: ERROR — {result!r}")
        else:
            print(f"  {name}: saved {result}")

    return results


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] getApplications isolation diagnostic\n")

    await run_test(
        "A — Liverpool + Warrington together (matches original known-good pair)",
        ["Liverpool City Council", "Warrington Borough Council"],
    )

    await run_test(
        "B — Warrington ALONE (rules out cross-domain session tainting)",
        ["Warrington Borough Council"],
    )

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)
    print("\nInterpretation:")
    print("  - If Warrington succeeds in BOTH tests: the 12-council run's")
    print("    volume/cross-domain pattern is the real trigger — solution")
    print("    is to split the council list into smaller batches/runs.")
    print("  - If Warrington fails even alone (Test B): something has")
    print("    genuinely changed at Warrington/the platform independent")
    print("    of how many councils we visit — needs its own fresh recon.")


if __name__ == "__main__":
    asyncio.run(main())
