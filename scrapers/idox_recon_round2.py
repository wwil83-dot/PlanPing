#!/usr/bin/env python3
"""
PlanFind — Idox recon round 2 (2026-07-20).

Follow-up to idox_multi_recon.py. That first pass caught its own mistake:
it only did page.goto() and checked for a results container immediately —
but production's real _scrape_month() flow selects the month dropdown,
clicks a "date received" radio, clicks Submit, THEN waits up to 25s for
results. Brighton and Brent's "no results container" finding was an
artifact of skipping that interaction, not a real bug — this script
replicates the ACTUAL production flow (same selectors, same order,
same waits, copied directly from idox_scraper.py) so results container
match/timeout evidence is real this time.

Also adds a monthly-list fallback probe for Renfrewshire, since its
'weekly' tag in idox_councils.py was never itself investigated — the
recon-round-1 error page doesn't explain WHY weekly was chosen, so this
checks whether the standard monthly flow works fine instead.

Tonbridge and Malling is NOT re-tested here — ERR_CONNECTION_RESET has
now reproduced identically across 3 independent contexts (2 nightly
batch runs + the round-1 recon), which is strong enough evidence on its
own without spending another run confirming a 4th time.
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


async def renfrewshire_monthly_probe(page, base_url: str):
    """Renfrewshire is tagged 'weekly' in idox_councils.py, but round-1
    recon's error page didn't explain why. Test whether the STANDARD
    monthly flow works fine instead — if it does, the weekly tag itself
    is the bug, not the weekly endpoint."""
    print("Testing whether Renfrewshire's standard MONTHLY flow works "
          "(its 'weekly' tag has never itself been verified)...")
    await full_monthly_flow(page, base_url, "renfrewshire_monthly_probe")


async def main():
    print("PlanFind Idox recon — round 2 (real form-submit flow)\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)

        for name, base_url, extra in [
            ("Brighton and Hove City Council", "https://planningapps.brighton-hove.gov.uk/online-applications", None),
            ("London Borough of Brent", "https://pa.brent.gov.uk/online-applications", None),
            ("Renfrewshire Council", "https://pl-bs.renfrewshire.gov.uk/online-applications", "monthly_probe"),
        ]:
            print(f"\n{'=' * 70}")
            print(f"RECON ROUND 2: {name}")
            print("=" * 70)
            context = await browser.new_context(**CONTEXT_OPTIONS)
            page = await context.new_page()

            if extra == "monthly_probe":
                await renfrewshire_monthly_probe(page, base_url)
            else:
                label = name.lower().replace(" ", "_")
                await full_monthly_flow(page, base_url, label)

            await context.close()

        await browser.close()

    print(f"\n{'=' * 70}")
    print("Round 2 recon complete.")


if __name__ == "__main__":
    asyncio.run(main())
