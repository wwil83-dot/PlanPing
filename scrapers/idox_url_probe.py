#!/usr/bin/env python3
"""
Idox candidate-URL probe (2026-10-04).

Medway's ODP register (planningregister.org/medway) turned out to be a
deliberately partial pilot — its own front page says only a limited set
of applications are published there, and it sends people to the primary
register. Third-party pages link to Medway's primary register as an Idox
PublicAccess portal at publicaccess1.medway.gov.uk (note the "1"); the
old idox_councils.py entry used publicaccess.medway.gov.uk, without it.

This runs the PRODUCTION scraper code (idox_scraper.IdoxPortal) against a
candidate URL, in monthly AND weekly mode, and prints how many
applications come back per month so the figures can be compared with
what the council's own site shows. It writes NOTHING to the database.
Add further (name, url) pairs to CANDIDATES to test other URLs the same way.
"""
import asyncio
import time
from collections import Counter

from playwright.async_api import async_playwright

import idox_scraper as scraper            # production code, unchanged

CANDIDATES = {
    "Medway Council": "https://publicaccess1.medway.gov.uk/online-applications",
}
DAYS_BACK = 60    # covers this month, last month and the one before


def month_counts(apps) -> dict:
    return dict(sorted(Counter((a.get("submitted_date") or "undated")[:7] for a in apps).items()))


def verdict(monthly_n: int, weekly_n: int) -> str:
    if monthly_n > 0:
        return "monthly mode works — add it to IDOX_COUNCILS as an ordinary entry"
    if weekly_n > 0:
        return "ONLY weekly mode returns data — add it with the \"weekly\" flag"
    return ("neither mode returns data — read the diagnostics printed above for the "
            "cause (connection block, challenge page, wrong structure)")


async def run_mode(browser, name, url, weekly: bool):
    portal = scraper.IdoxPortal(name, url, 0, use_weekly_list=weekly)
    t0 = time.monotonic()
    try:
        apps = await portal.scrape(browser, days_back=DAYS_BACK)
    except Exception as e:
        print(f"    [{name}] ✗ {'weekly' if weekly else 'monthly'} run raised "
              f"{type(e).__name__}: {str(e)[:150]}")
        apps = []
    return {"n": len(apps), "secs": time.monotonic() - t0, "by_month": month_counts(apps),
            "sample": [(a.get("reference"), a.get("submitted_date"), a.get("status")) for a in apps[:3]]}


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        results = []
        for name, url in CANDIDATES.items():
            print(f"\n{'=' * 60}\n{name}\n  {url}\n{'=' * 60}")
            out = {}
            for label, weekly in (("monthly", False), ("weekly", True)):
                print(f"  --- {label} mode ---")
                out[label] = await run_mode(browser, name, url, weekly)
                r = out[label]
                print(f"  {label}: {r['n']} application(s) in {r['secs']:.0f}s")
                print(f"     by month received: {r['by_month']}")
                print(f"     sample: {r['sample']}")
                await asyncio.sleep(3)
            results.append((name, url, out))
        await browser.close()

    print(f"\n\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for name, url, out in results:
        m, w = out["monthly"]["n"], out["weekly"]["n"]
        print(f"{name}  ({url})\n    monthly={m}  weekly={w}  ->  {verdict(m, w)}")
    print("\nProbe complete (nothing was written to the database).")


if __name__ == "__main__":
    asyncio.run(main())
