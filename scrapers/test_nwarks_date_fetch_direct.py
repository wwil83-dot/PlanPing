#!/usr/bin/env python3
"""
PlanFind — direct test of esl_scraper.py's real fetch_received_dates()
against North Warwickshire (2026-08-25).

Real, honest gap: neither of the last 2 nightly runs found a genuinely
new North Warwickshire application, so the disclaimer-handling fix
added to fetch_received_dates()/recheck_pending() has never actually
been exercised. Rather than wait indefinitely for a natural new
application to appear, this calls the REAL function directly against
a real, already-known North Warwickshire reference, to get a direct,
immediate answer instead of an inferred one.
"""
import asyncio
import sys
from datetime import datetime, timezone

from playwright.async_api import async_playwright

sys.path.insert(0, ".")
from esl_scraper import fetch_received_dates, BROWSER_ARGS

# Real, already-confirmed reference from an earlier run's own database
# rows — genuinely exists, just being reused here as a direct test
# subject rather than waiting for a new one.
TEST_APP = {
    "reference": "2026/0661/LAWP",
    "council_url": "https://planning.northwarks.gov.uk/Planning/Display?applicationNumber=2026%2F0661%2FLAWP",
}


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Direct fetch_received_dates test "
          f"— North Warwickshire\n")
    print(f"Testing against real reference: {TEST_APP['reference']}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        results = await fetch_received_dates(browser, "North Warwickshire Borough Council", [TEST_APP])

        await browser.close()

    print(f"\n{'=' * 70}")
    print(f"Real result: {results}")
    if TEST_APP["reference"] in results:
        print(f"✓ CONFIRMED WORKING — found a real date: {results[TEST_APP['reference']]}")
    else:
        print(f"⚠ NO DATE FOUND — the disclaimer fix may not be working correctly, "
              f"or this specific reference genuinely has no parseable date")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
