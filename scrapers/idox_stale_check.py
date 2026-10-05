#!/usr/bin/env python3
"""
Idox stale-council check (2026-10-04).

Nine councils are on the nightly Idox list but have stopped saving (or,
for Gloucester and Newham, never saved after their URLs were corrected
on 28 July). The nightly run only logs a failure; it doesn't say which
of several causes it is. This runs the PRODUCTION scraper code on just
those councils, in BOTH monthly mode (what the nightly run uses) and
weekly mode, and reports what each returns. It writes NOTHING to the
database. Every diagnostic already built into idox_scraper.py (WAF
signature, container missing, too-many-results, month dropdown) prints
as normal, tagged with the council name.

Outcomes per council:
  - monthly returns data   -> the portal is fine; the nightly run isn't
                              reaching or saving it (time-budget skip,
                              intermittent block)
  - ONLY weekly returns data -> switch the council to weekly mode, the
                              same fix already applied to East Riding
  - neither returns data   -> read the diagnostics printed above it
"""
import asyncio
import time

from playwright.async_api import async_playwright

import idox_scraper as scraper            # production code, unchanged
from idox_councils import IDOX_COUNCILS, COUNCIL_DB_IDS

CHECK = [
    "Gloucester City Council",
    "London Borough of Newham",
    "Gosport Borough Council",
    "Bolsover District Council",
    "North East Derbyshire District Council",
    "Blaby District Council",
    "Melton Borough Council",
    "Derbyshire Dales District Council",
    "Hinckley and Bosworth Borough Council",
]


def find_entry(name):
    """(url, extra_search_param) for a council, or None if it isn't in
    the list. Handles the 2-, 3- and 4-tuple entry formats."""
    for entry in IDOX_COUNCILS:
        if entry[0] == name:
            extra = entry[3] if len(entry) == 4 else ""
            return entry[1], extra
    return None


def verdict(monthly_n: int, weekly_n: int) -> str:
    if monthly_n > 0:
        return ("monthly mode works — the portal is fine; look at why the nightly "
                "run isn't saving it (time-budget skip? intermittent block?)")
    if weekly_n > 0:
        return "ONLY weekly mode returns data — switch this council to weekly mode"
    return ("neither mode returns data — read the diagnostics printed above for "
            "the real cause (block, wrong page structure, too many results)")


async def run_mode(browser, name, url, extra, weekly: bool):
    portal = scraper.IdoxPortal(name, url, COUNCIL_DB_IDS.get(name, 0),
                                use_weekly_list=weekly, extra_search_param=extra)
    t0 = time.monotonic()
    try:
        apps = await portal.scrape(browser, days_back=14)
    except Exception as e:
        print(f"    [{name}] ✗ {('weekly' if weekly else 'monthly')} run raised "
              f"{type(e).__name__}: {str(e)[:150]}")
        apps = []
    secs = time.monotonic() - t0
    sample = [(a.get("reference"), a.get("submitted_date"), a.get("status"))
              for a in apps[:3]]
    return {"n": len(apps), "secs": secs, "sample": sample}


async def check_one(browser, name):
    entry = find_entry(name)
    if entry is None:
        print(f"\n[{name}] ⚠ not found in IDOX_COUNCILS — renamed or removed?")
        return name, None
    url, extra = entry
    print(f"\n{'=' * 60}\n{name}\n  {url}\n{'=' * 60}")
    out = {}
    for label, weekly in (("monthly", False), ("weekly", True)):
        print(f"  --- {label} mode ---")
        out[label] = await run_mode(browser, name, url, extra, weekly)
        r = out[label]
        print(f"  {label}: {r['n']} application(s) in {r['secs']:.0f}s  sample: {r['sample']}")
        await asyncio.sleep(3)
    return name, out


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        results = []
        for name in CHECK:
            results.append(await check_one(browser, name))
        await browser.close()

    print(f"\n\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for name, out in results:
        if out is None:
            print(f"{name}\n    not in IDOX_COUNCILS")
            continue
        m, w = out["monthly"]["n"], out["weekly"]["n"]
        print(f"{name}\n    monthly={m:3}  weekly={w:3}  ->  {verdict(m, w)}")
    print("\nDiagnostic complete (nothing was written to the database).")


if __name__ == "__main__":
    asyncio.run(main())
