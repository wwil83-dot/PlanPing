#!/usr/bin/env python3
"""
PlanFind — advanced search real structure/content check (2026-09-27).

Real question: all 15 real Boston wards returned exactly 0 real
applications when run through the full scraper, with the "no real
ul#searchresults > li.searchresult items found" diagnostic firing —
the same structural signal, 15 times in a row, which is a much
stronger signal of a systematic problem than 15 genuinely empty
wards. The scraper's parser was built around the monthly list's
confirmed structure, but the advanced search results page was never
directly confirmed to use the same structure — this checks directly
whether real results exist in a different format, or whether the
search genuinely found nothing (and if so, what real message the page
shows).
"""
import asyncio
from datetime import date, timedelta

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
        await ward_select.select_option(value="KIFR")
        print("Real ward selected: Kirton And Frampton (KIFR)")

        today = date.today()
        start = today - timedelta(days=90)
        await page.fill("input[name='date(applicationReceivedStart)']", start.strftime("%d/%m/%Y"), timeout=5_000)
        await page.fill("input[name='date(applicationReceivedEnd)']", today.strftime("%d/%m/%Y"), timeout=5_000)
        print(f"Real date range: {start.strftime('%d/%m/%Y')} to {today.strftime('%d/%m/%Y')} (90 days)")

        submit = page.locator("input[type='submit'], button[type='submit']")
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit.first.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"\nReal results URL: {page.url}")
        print(f"Real results page title: {await page.title()}\n")

        body_text = await page.locator("body").inner_text()
        print(f"Real full body text (first 3000 chars):")
        print(repr(body_text[:3000]))

        results_ul = page.locator("ul#searchresults")
        print(f"\nReal ul#searchresults found: {await results_ul.count()}")

        addresses = page.locator("p.address")
        print(f"Real p.address elements found: {await addresses.count()}")

        ref_elements = page.locator("text=/Ref\\.\\s*No/i")
        print(f"Real elements containing 'Ref. No' text: {await ref_elements.count()}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
