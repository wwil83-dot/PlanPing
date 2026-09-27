#!/usr/bin/env python3
"""
PlanFind — shared portal ward-filter verification (2026-09-27).

Real, confirmed context: Boston Borough Council's 15 real wards have
been precisely matched against this shared portal's real ward
dropdown (Coastal=COAS, Fenside=FENS, Fishtoft=FISH, Five
Village=FIVE, Kirton And Frampton=KIFR, Old Leake And Wrangle=OLWR,
Skirbeck=SKIR, St Thomas'=STTO, Staniland=STAN, Station=STAT,
Swineshead And Holland Fen=SWHF, Trinity Ward Boston=TRINB, West=WEST,
Witham=WITM, Wyberton=WYBE) — sourced from the council's own ward
poster and real election records, with two genuine false positives
(a South Holland ward matched by a stray "west" substring, and a
non-Boston "Trinity Ward" without the "Boston" suffix) correctly
excluded.

This tests whether selecting a real ward via the actual form and
submitting produces genuinely filtered results (only that ward's real
addresses), confirming the approach works before rebuilding the
scraper around it.
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

        print("=" * 60)
        print("TEST 1: Real form interaction — select Coastal Ward, submit")
        print("=" * 60)
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
        if await submit.count() > 0:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await submit.first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            print(f"Real results URL: {page.url}")
            body_text = await page.locator("body").inner_text()

            import re
            count_match = re.search(r"of\s+(\d+)", body_text)
            print(f"Real total result count found: {count_match.group(1) if count_match else 'not found'}")

            addresses = page.locator("p.address")
            addr_count = await addresses.count()
            print(f"Real p.address elements found: {addr_count}")
            for i in range(min(addr_count, 10)):
                text = await addresses.nth(i).text_content()
                print(f"  [{i}] {text.strip()!r}")
        else:
            print("⚠ No real submit control found")

        print("\n" + "=" * 60)
        print("TEST 2: Direct URL with ward query parameter")
        print("=" * 60)
        direct_url = f"{BASE_URL}/monthlyListResults.do?action=firstPage&searchCriteria.ward=COAS"
        await page.goto(direct_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        print(f"Real URL after direct navigation: {page.url}")
        addresses2 = page.locator("p.address")
        addr_count2 = await addresses2.count()
        print(f"Real p.address elements found via direct URL: {addr_count2}")
        for i in range(min(addr_count2, 5)):
            text = await addresses2.nth(i).text_content()
            print(f"  [{i}] {text.strip()!r}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
