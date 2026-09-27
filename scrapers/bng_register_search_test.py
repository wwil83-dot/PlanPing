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

        print(f"Real page title: {await page.title()}")

        inputs = await page.locator("input, select").all()
        print(f"\nReal form controls found: {len(inputs)}")
        for inp in inputs:
            name = await inp.get_attribute("name")
            itype = await inp.get_attribute("type")
            placeholder = await inp.get_attribute("placeholder")
            print(f"  name={name!r} type={itype!r} placeholder={placeholder!r}")

        search_box = page.locator("input[type='search'], input[type='text']").first
        if await search_box.count() > 0:
            await search_box.fill("Boston Borough Council", timeout=5_000)
            print("\nReal search term entered: 'Boston Borough Council'")

            submit = page.locator("button:has-text('Search')")
            if await submit.count() > 0:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    await submit.first.click(timeout=5_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass

                print(f"\nReal results URL: {page.url}")

                # REAL FIX — confirmed via the actual run: this page is
                # client-side rendered, and networkidle alone finished
                # before the real AJAX-loaded results appeared (body
                # text just showed "Loading Message..."). Waiting for
                # that real loading text to genuinely disappear instead.
                try:
                    await page.wait_for_selector("text=Loading Message", state="detached", timeout=15_000)
                except PlaywrightTimeout:
                    print("⚠ 'Loading Message...' never disappeared within 15s")
                await asyncio.sleep(2)

                print(f"Real results page title: {await page.title()}")
                body_text = await page.locator("body").inner_text()
                print(f"\nReal body text (first 3000 chars):")
                print(repr(body_text[:3000]))
            else:
                print("⚠ No real Search button found after entering the term")
        else:
            print("⚠ No real search input found")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
