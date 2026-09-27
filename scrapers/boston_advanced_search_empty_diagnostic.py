#!/usr/bin/env python3
"""
PlanFind — advanced search empty-result diagnostic (2026-09-27).

Real, confirmed context: selecting the real Coastal ward (COAS) and
submitting the advanced search form landed on advancedSearchResults.do
with zero real p.address elements found — a different, more
informative signal than "genuinely no Boston applications", since it
suggests the submission itself may not have worked as expected (e.g.
a real validation message requiring more criteria, similar to the
real "Too many results" / empty-checkbox behaviour already confirmed
for Vale of White Horse and South Oxfordshire earlier tonight).

This dumps the real body text and title of the actual results page
directly, so the real reason can be seen rather than guessed at.
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

BASE_URL = "https://publicaccess.e-lindsey.gov.uk/online-applications"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        advanced_url = f"{BASE_URL}/search.do?action=advanced"
        await page.goto(advanced_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        ward_select = page.locator("select[name='searchCriteria.ward']")
        await ward_select.select_option(value="COAS")
        print("Real ward selected: Coastal (COAS)")

        submit = page.locator("input[type='submit'], button[type='submit']")
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit.first.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"Real results URL: {page.url}")
        print(f"Real results page title: {await page.title()}\n")

        body_text = await page.locator("body").inner_text()
        print(f"Real full body text (first 3000 chars):")
        print(repr(body_text[:3000]))

        error_like = page.locator(".error, .validation-summary-errors, [class*='error' i]")
        error_count = await error_like.count()
        print(f"\nReal elements with 'error' in class: {error_count}")
        for i in range(min(error_count, 5)):
            text = await error_like.nth(i).text_content()
            print(f"  [{i}] {text.strip()!r}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
