#!/usr/bin/env python3
"""
Real BNG register search behaviour test (2026-09-27).

Real question: the register's own search form requires a reference
number, LPA, habitat type, or responsible body — no visible "browse
all" option. This tests whether searching by a real LPA name (e.g.
"Boston Borough Council") returns that council's full list of
registered sites, which would make per-council enumeration a genuine,
practical way to cover the whole register — the same kind of approach
already proven across ~300 councils tonight, just applied to a
different real government dataset.
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

BASE_URL = "https://environment.data.gov.uk/biodiversity-net-gain"


async def search_lpa(page, lpa_name: str):
    print(f"\n{'=' * 60}")
    print(f"Testing: {lpa_name}")
    print('=' * 60)

    search_box = page.locator("input[type='search'], input[type='text']").first
    await search_box.fill("", timeout=5_000)
    await search_box.fill(lpa_name, timeout=5_000)

    submit = page.locator("button:has-text('Search')")
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
        await submit.first.click(timeout=5_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    print(f"Real results URL: {page.url}")

    # REAL FIX — confirmed via the actual run: waiting for "Loading
    # Message..." to disappear wasn't reliable — both "SITES FOUND"
    # results still showed that exact loading text in their captured
    # excerpt, meaning the page was still mid-load, not genuinely
    # confirmed to have real results. Waiting for one of the two real,
    # concrete outcomes instead (the "no results" message, or the
    # loading text genuinely gone), with a longer timeout in case a
    # real, larger result set simply takes longer to render than an
    # empty one does.
    try:
        await page.wait_for_function(
            "() => !document.body.innerText.includes('Loading Message')",
            timeout=30_000,
        )
    except PlaywrightTimeout:
        print("⚠ Still showing 'Loading Message...' after 30s — real content may not have loaded")
    await asyncio.sleep(1)

    body_text = await page.locator("body").inner_text()
    still_loading = "Loading Message" in body_text
    if still_loading:
        print("Real result: INCONCLUSIVE — still loading after 30s, not a confirmed result")
    elif "No search results found" in body_text:
        print("Real result: NO SITES FOUND (confirmed — loading finished, real 'no results' message shown)")
    else:
        print("Real result: SITES FOUND (confirmed — loading finished, no 'no results' message)")
        print(f"Real body excerpt: {body_text[:1500]!r}")

    # Navigate back to a fresh search page before the next term.
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        accept = page.locator("button:has-text('Accept all cookies')")
        if await accept.count() > 0:
            await accept.first.click(timeout=5_000)
            print("Real cookie banner accepted")

        # Real, deliberately mixed set: large, high-development
        # councils where zero sites after 2+ years of mandatory BNG
        # would be a genuine surprise, plus Boston again as a direct
        # re-check against the exact same confirmed search flow.
        real_test_councils = [
            "Birmingham City Council",
            "Manchester City Council",
            "Leeds City Council",
            "Cornwall Council",
            "Boston Borough Council",
        ]

        for lpa_name in real_test_councils:
            await search_lpa(page, lpa_name)

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
