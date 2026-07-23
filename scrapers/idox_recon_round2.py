#!/usr/bin/env python3
"""
PlanFind — Idox recon round 3 (2026-07-23).

Follow-up to idox_multi_recon.py's second run, which repeated round 1's
original mistake — my own error, worth being upfront about: it only did
page.goto() and checked for a results container immediately, the same
false-alarm pattern round 2 (this file) already fixed for Brighton/Brent
back on 2026-07-20. That earlier fix never got carried forward when
idox_multi_recon.py was updated with a new target list, so Brighton,
Bolsover District, and North East Derbyshire District all came back with
the same "no results container" non-finding — real page loads, working
month/parish/ward dropdowns, just never actually submitted.

This round replicates the ACTUAL production flow (same selectors, same
order, same waits, copied directly from idox_scraper.py) for all 4
councils still showing this pattern: Brighton and Hove, Bolsover
District, North East Derbyshire District, and London Borough of Brent
(re-tested since its earlier "too many results" diagnosis should still
hold, but worth confirming it's still the real cause rather than
assuming).

Renfrewshire is NOT re-tested here — its mode-switch fix (weekly →
monthly) has held for good across multiple real production runs, and
it's no longer on the health-check's flagged list.

Gosport, Pendle, Exeter (genuine page-load timeouts in isolation) and
Solihull (ERR_CONNECTION_REFUSED, consistent across many independent
observations all session) are NOT covered by this script — those are a
different failure category (network-level, not results-container) with
strong enough evidence already to treat as settled without further
recon.
"""
import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

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

RESULTS_SELECTOR = (
    "ul.searchresults, #searchResultsContainer, .searchresults, "
    ".no-results, #searchResultsForm"
)

# Copied verbatim from idox_scraper.py's _scrape_month() so this recon
# tests the EXACT same interaction production uses — not a simplified
# approximation.
MONTH_SELECTORS = [
    "select[id='month']", "select[name='month']",
    "select[id='searchCriteria.monthYearIndex']",
    "select[name='searchCriteria.monthYearIndex']",
    "select[name*='monthYear']", "select[id*='monthYear']",
]
RADIO_SELECTORS = [
    "input#dateReceived", "input[value='dateReceived']",
    "input[id*='Received'][type='radio']", "input[name*='date'][value*='eceiv']",
    "label:has-text('Received') input", "input[value='dc']", "input[value='DC']",
    "input[value='dv']", "input[value='DV']",
    "input[id*='Validated'][type='radio']", "label:has-text('Validated') input",
]
SUBMIT_SELECTORS = [
    "#monthlyListForm input[type='submit']", "#monthlyListForm input.button",
    "form input[type='submit']", "form button[type='submit']", "input.button",
]


async def full_monthly_flow(page, base_url: str, label: str):
    """Replicates _scrape_month()'s real interaction: load, select month
    index 0, click date-received radio, submit, wait for results."""
    url = f"{base_url}/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"
    print(f"Navigating to: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except PlaywrightTimeout:
        print("  ⚠ Page load timeout on initial navigation.")
        return
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        return

    try:
        await page.wait_for_selector("#monthlyListForm, form, ul.searchresults", timeout=12_000)
    except PlaywrightTimeout:
        title = await page.title()
        print(f"  ⚠ Nothing loaded before form step — title: {title!r}")
        return

    initial_title = await page.title()
    print(f"  Initial (pre-submit) page title: {initial_title!r}")

    dropdown_found = False
    for sel in MONTH_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.select_option(index=0)
                dropdown_found = True
                print(f"  Month dropdown matched via: {sel!r}")
                break
        except Exception:
            continue
    if not dropdown_found:
        print("  ⚠ Month dropdown NOT found by any known selector (would trigger "
              "MONTH DROPDOWN DIAGNOSTIC in production).")

    radio_found = False
    for sel in RADIO_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                radio_found = True
                print(f"  Date-type radio matched via: {sel!r}")
                break
        except Exception:
            continue
    if not radio_found:
        print("  (no date-type radio matched — may be fine if the portal has none)")

    submitted = False
    for sel in SUBMIT_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                submitted = True
                print(f"  Submit matched via: {sel!r} — clicked")
                break
        except Exception:
            continue
    if not submitted:
        print("  ⚠ No submit button matched by any known selector — form never submitted.")

    try:
        await page.wait_for_selector(RESULTS_SELECTOR, timeout=25_000)
        final_title = await page.title()
        print(f"  ✓ Results container appeared. Post-submit title: {final_title!r}")
        body_text = await page.locator("body").inner_text()
        snippet = " ".join(body_text.split())[:300]
        print(f"  Body snippet: {snippet!r}")
    except PlaywrightTimeout:
        final_title = await page.title()
        print(f"  ⚠ RESULTS TIMEOUT after real submit — title: {final_title!r}")
        print("  This matches production's exact failure mode. The form interaction")
        print("  itself completed (or didn't — see above), but no results container")
        print("  appeared within 25s of a REAL submit — not a recon-script artifact.")
        body_text = await page.locator("body").inner_text()
        snippet = " ".join(body_text.split())[:400]
        print(f"  Body snippet at timeout: {snippet!r}")

    html = await page.content()
    path = f"/tmp/idox_recon2_{label}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Full HTML saved: {path} ({len(html)} chars)")


async def main():
    print("PlanFind Idox recon — round 3 (real form-submit flow)\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)

        for name, base_url in [
            ("Brighton and Hove City Council", "https://planningapps.brighton-hove.gov.uk/online-applications"),
            ("Bolsover District Council", "https://publicaccess.bolsover.gov.uk/online-applications"),
            ("North East Derbyshire District Council", "https://planapps-online.ne-derbyshire.gov.uk/online-applications"),
            ("London Borough of Brent", "https://pa.brent.gov.uk/online-applications"),
        ]:
            print(f"\n{'=' * 70}")
            print(f"RECON ROUND 3: {name}")
            print("=" * 70)
            context = await browser.new_context(**CONTEXT_OPTIONS)
            page = await context.new_page()

            label = name.lower().replace(" ", "_")
            await full_monthly_flow(page, base_url, label)

            await context.close()

        await browser.close()

    print(f"\n{'=' * 70}")
    print("Round 3 recon complete.")


if __name__ == "__main__":
    asyncio.run(main())
