#!/usr/bin/env python3
"""
PlanFind — Bright Data Browser API test (2026-08-16).

PURPOSE: Seven rounds of testing ScraperAPI's Web Unlocker-style plain
HTTP approach against a CSRF-protected, multi-step Idox form landed on
a consistent, genuine 403 either way (explicit cookie forwarding, or
trusting ScraperAPI's own session handling entirely). Browser API is a
fundamentally different approach — a REAL remote browser, connected to
via Playwright's own connect_over_cdp(), meaning our actual, already-
proven form-interaction logic from idox_scraper.py can run essentially
unchanged: select the month dropdown, click the real "date type" radio
button, click submit, wait for real results. No manual CSRF/cookie
reconstruction needed at all — a real browser handles that the same
way a real visitor's browser would.

Real, still-open question this settles: does Bright Data's residential
network's government-site KYC restriction (confirmed earlier tonight
against Web Unlocker specifically) also apply to Browser API, or is it
specific to the residential proxy product? Genuinely unverified before
this test — worth finding out directly rather than assuming either way.

Credentials read from an environment variable — never hardcoded here,
matching the same convention already used for SUPABASE_URL/SUPABASE_KEY
and every other credential this whole project has ever used.
"""
import asyncio
import os
import sys
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

AUTH = os.environ.get("BRIGHTDATA_BROWSER_AUTH", "")
ENDPOINT = f"wss://{AUTH}@brd.superproxy.io:9222" if AUTH else ""

# Real field structure confirmed via ScraperAPI's form extraction
# (round 4) — same three councils, for a fair, direct comparison
# against everything already tried.
TARGETS = [
    ("Aberdeenshire Council",
     "https://upa.aberdeenshire.gov.uk/online-applications"),
    ("Babergh District Council",
     "https://planning.baberghmidsuffolk.gov.uk/online-applications"),
    ("Argyll and Bute Council",
     "https://publicaccess.argyll-bute.gov.uk/online-applications"),
]

REAL_DATA_MARKERS = [
    "application type", "date received", "ref. no", "decision",
]
RESTRICTION_MARKERS = [
    "kyc", "not permitted", "restricted", "not available", "blocked domain",
]


async def scrape_one_council(browser, name: str, base_url: str) -> dict:
    print(f"\n{'-' * 70}")
    print(f"{name}")
    print("-" * 70)

    page = await browser.new_page()
    page.set_default_navigation_timeout(120_000)  # real browsers over CDP are slower

    try:
        url = f"{base_url}/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"
        print(f"  Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded")

        title = await page.title()
        print(f"  Real page title after navigation: {title!r}")

        # Real form interaction — the SAME selectors already confirmed
        # via ScraperAPI's form extraction (round 4): id='month',
        # dateType radio with value DC_Validated, real submit button.
        try:
            await page.locator("select#month").select_option(index=0)
            print(f"  Month dropdown selected (index 0)")
        except Exception as e:
            print(f"  ⚠ Could not select month dropdown: {e}")

        try:
            await page.locator("input#dateValidated").check()
            print(f"  'Date validated' radio button checked")
        except Exception as e:
            print(f"  ⚠ Could not check the date-type radio button: {e}")

        try:
            await page.locator("input[type='submit']").first.click()
            print(f"  Submit button clicked")
        except Exception as e:
            print(f"  ⚠ Could not click submit: {e}")

        try:
            await page.wait_for_selector(
                "ul.searchresults, #searchResultsContainer, .searchresults, "
                ".no-results, #searchResultsForm",
                timeout=30_000,
            )
            print(f"  Real results container appeared")
        except PlaywrightTimeout:
            print(f"  ⚠ No results container appeared within 30s")

        html = await page.content()
        html_lower = html.lower()
        real_data_hits = [m for m in REAL_DATA_MARKERS if m in html_lower]
        restriction_hits = [m for m in RESTRICTION_MARKERS if m in html_lower]
        final_title = await page.title()

        print(f"  Final page title: {final_title!r}")
        print(f"  Real content length: {len(html):,} chars")
        print(f"  Real Idox data markers found: {real_data_hits if real_data_hits else 'NONE'}")
        print(f"  Restriction/policy markers found: {restriction_hits if restriction_hits else 'none'}")

        snippet = " ".join(html.split())[:1500]
        print(f"  Content snippet: {snippet!r}")

        path = f"/tmp/browserapi_{name.lower().replace(' ', '_')}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Full HTML saved: {path}")

        return {
            "title": final_title,
            "real_data_hits": real_data_hits,
            "restriction_hits": restriction_hits,
            "length": len(html),
        }
    except Exception as e:
        print(f"  ⚠ Real error during scrape: {type(e).__name__}: {e}")
        return {"error": str(e)}
    finally:
        await page.close()


async def main():
    print("BRIGHT DATA BROWSER API — real test using our own proven form logic")
    print("against the same three confirmed-blocked councils used throughout")
    print("tonight's ScraperAPI testing, for a fair, direct comparison.\n")

    if not AUTH:
        print("Set BRIGHTDATA_BROWSER_AUTH as an environment variable before")
        print("running this — never hardcode real credentials into this file.")
        sys.exit(1)

    async with async_playwright() as p:
        print("Connecting to Browser API...")
        try:
            browser = await p.chromium.connect_over_cdp(ENDPOINT)
        except Exception as e:
            print(f"⚠ Could not connect to Browser API at all: {type(e).__name__}: {e}")
            sys.exit(1)
        print("Connected.\n")

        results = {}
        for name, base_url in TARGETS:
            results[name] = await scrape_one_council(browser, name, base_url)

        await browser.close()

    print(f"\n\n{'=' * 70}")
    print("REAL SUMMARY — judge for yourself from the evidence above:")
    print("=" * 70)
    for name, result in results.items():
        if result.get("error"):
            print(f"  {name}: FAILED — {result['error']}")
        elif result.get("restriction_hits"):
            print(f"  {name}: LIKELY HIT A RESTRICTION — {result['restriction_hits']}")
        elif result.get("real_data_hits"):
            print(f"  {name}: REAL SUCCESS — genuine Idox data markers found")
        else:
            print(f"  {name}: loaded but no real-data markers — check the saved HTML directly")


if __name__ == "__main__":
    asyncio.run(main())
