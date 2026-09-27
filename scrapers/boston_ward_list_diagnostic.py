#!/usr/bin/env python3
"""
PlanFind — shared portal complete ward list diagnostic (2026-09-27).

Real, confirmed context: a partial ward dropdown dump earlier only
showed the first 15 of what's likely 50-100+ real options (all three
partner councils' wards combined in one flat list). This dumps every
real option so Boston Borough's confirmed 15 wards (Coastal, Fenside,
Fishtoft, Five Villages, Kirton and Frampton, Old Leake and Wrangle,
Skirbeck, St Thomas', Staniland, Station, Swineshead and Holland Fen,
Trinity, West, Witham, Wyberton — sourced from the council's own
ward poster and real election records) can be matched precisely
against this portal's own exact option text and values.
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
        if await ward_select.count() == 0:
            print("⚠ No real ward select found")
            await context.close()
            await browser.close()
            return

        options = ward_select.locator("option")
        count = await options.count()
        print(f"Real total ward options found: {count}\n")

        real_confirmed_boston_wards = [
            "coastal", "fenside", "fishtoft", "five village", "kirton and frampton",
            "kirton & frampton", "old leake and wrangle", "old leake & wrangle",
            "skirbeck", "st thomas", "staniland", "station",
            "swineshead and holland fen", "swineshead & holland fen",
            "trinity", "west", "witham", "wyberton",
        ]

        for i in range(count):
            opt = options.nth(i)
            text = await opt.text_content()
            value = await opt.get_attribute("value")
            text_clean = (text or "").strip()
            is_likely_boston = any(w in text_clean.lower() for w in real_confirmed_boston_wards)
            marker = "  <-- LIKELY BOSTON" if is_likely_boston else ""
            print(f"  [{i}] text={text_clean!r} value={value!r}{marker}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
