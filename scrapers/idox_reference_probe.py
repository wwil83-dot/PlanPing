#!/usr/bin/env python3
"""
Why does the Babergh / Mid Suffolk portal save the DESCRIPTION as the reference?
(2026-10-08)

Babergh holds 730 rows and 727 of them have a reference longer than 40
characters (Mid Suffolk: 548 of 550); the newest was saved today. Every other
Idox council with this symptom stopped on about 16 July. So on this portal the
production parser is not finding the reference in the list markup.

This runs the PRODUCTION scraper for ONE council over a short window while
recording, for every list result it parses, the raw HTML of that result next to
the reference production extracted from it. It prints a few bad ones and a few
good ones side by side, so the difference in markup is visible. Writes NOTHING
to the database. Needs idox_scraper.py and idox_councils.py alongside it.

Env: COUNCIL (default "Babergh District Council"), PROBE_DAYS (default 7).
"""
import asyncio
import os

from playwright.async_api import async_playwright

import idox_scraper as scraper
from idox_councils import IDOX_COUNCILS

COUNCIL = os.environ.get("COUNCIL", "Babergh District Council")
DAYS = int(os.environ.get("PROBE_DAYS", "7"))
captured: list = []                    # (reference production extracted, raw HTML of the item)


def looks_bad(ref) -> bool:
    ref = ref or ""
    return len(ref) > 40 or not any(ch.isdigit() for ch in ref)


def find_parser():
    fn = getattr(scraper, "_parse_result", None)
    if fn is None:
        names = [n for n in dir(scraper) if "parse" in n.lower()]
        raise SystemExit(f"idox_scraper has no _parse_result. Functions with 'parse' in the name: {names}")
    return fn


def install_spy():
    original = find_parser()
    if getattr(original, "_reference_spy", False):
        return                          # already installed: never wrap twice (it would count every result twice)

    def spy(item, *args, **kwargs):
        result = original(item, *args, **kwargs)
        try:
            ref = (result or {}).get("reference") if isinstance(result, dict) else None
            captured.append((ref, str(item)))
        except Exception:
            pass
        return result

    spy._reference_spy = True
    scraper._parse_result = spy        # the page parser looks this name up when it is called


def summarise(items, show_bad=3, show_good=2):
    bad = [(r, h) for r, h in items if looks_bad(r)]
    good = [(r, h) for r, h in items if not looks_bad(r)]
    return bad, good, bad[:show_bad], good[:show_good]


async def main():
    entry = next((e for e in IDOX_COUNCILS if e[0] == COUNCIL), None)
    if entry is None:
        raise SystemExit(f"{COUNCIL!r} is not in IDOX_COUNCILS")
    url, extra = entry[1], (entry[3] if len(entry) == 4 else "")
    install_spy()
    portal = scraper.IdoxPortal(COUNCIL, url, 0, extra_search_param=extra)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n{COUNCIL}\n  {url}\n  window: {DAYS} days\n")
        apps = await portal.scrape(browser, days_back=DAYS)
        await browser.close()

    bad, good, show_bad, show_good = summarise(captured)
    print(f"\n{'=' * 60}\n{len(captured)} list results parsed: {len(good)} with a normal reference, "
          f"{len(bad)} where the 'reference' is long or has no digits\n{'=' * 60}")
    for label, rows in (("BAD", show_bad), ("GOOD", show_good)):
        for ref, html in rows:
            print(f"\n--- {label}: production extracted reference = {ref!r}")
            print(html[:1800] + (" …[cut]" if len(html) > 1800 else ""))
    print(f"\nProbe complete (nothing was written to the database). scrape() returned {len(apps)} applications.")


if __name__ == "__main__":
    asyncio.run(main())
