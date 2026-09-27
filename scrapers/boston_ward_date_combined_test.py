#!/usr/bin/env python3
"""
PlanFind — ward + date range combined verification (2026-09-27).

Real, confirmed context: selecting a real ward alone (Coastal/COAS)
returned a real, explicit validation message: "Too many results
found. Please enter some more parameters." — confirming the ward
filter itself works correctly, it's just matching the portal's entire
history with no date range applied. This finds the real date field
names on the advanced form directly (rather than guessing at Idox's
usual naming convention, which may differ on this specific
installation), then tests ward + a real 30-day date range together to
confirm this actually produces real, genuinely filtered results.
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

        print("=" * 60)
        print("STEP 1: Real date field names on the advanced form")
        print("=" * 60)
        date_inputs = await page.locator("input[type='text'], input[type='date'], input[name*='date' i], input[name*='Date' i]").all()
        real_date_fields = []
        for inp in date_inputs:
            name = await inp.get_attribute("name")
            if name and "date" in name.lower():
                real_date_fields.append(name)
        print(f"Real date-related input names found: {real_date_fields}\n")

        print("=" * 60)
        print("STEP 2: Real test — Coastal ward + last 30 days together")
        print("=" * 60)
        ward_select = page.locator("select[name='searchCriteria.ward']")
        await ward_select.select_option(value="COAS")
        print("Real ward selected: Coastal (COAS)")

        today = date.today()
        start = today - timedelta(days=30)

        if real_date_fields:
            # REAL FIX — the real fields use "Start"/"End" (e.g.
            # date(applicationReceivedStart)), not "from"/"to" as
            # assumed — that mismatch meant neither field matched
            # last time, and no date was actually filled in at all.
            from_field = next((f for f in real_date_fields
                                if "received" in f.lower() and "start" in f.lower()), None)
            to_field = next((f for f in real_date_fields
                              if "received" in f.lower() and "end" in f.lower()), None)
            print(f"Real 'start' field: {from_field!r}, real 'end' field: {to_field!r}")
            if from_field:
                await page.fill(f"input[name='{from_field}']", start.strftime("%d/%m/%Y"), timeout=5_000)
            if to_field:
                await page.fill(f"input[name='{to_field}']", today.strftime("%d/%m/%Y"), timeout=5_000)

        submit = page.locator("input[type='submit'], button[type='submit']")
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit.first.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"\nReal results URL: {page.url}")
        body_text = await page.locator("body").inner_text()

        error_like = page.locator("[class*='error' i]")
        if await error_like.count() > 0:
            error_text = await error_like.first.text_content()
            print(f"Real error/validation message still present: {error_text.strip()!r}")
        else:
            print("No real error/validation message found this time")

        addresses = page.locator("p.address")
        addr_count = await addresses.count()
        print(f"Real p.address elements found: {addr_count}")
        for i in range(min(addr_count, 10)):
            text = await addresses.nth(i).text_content()
            print(f"  [{i}] {text.strip()!r}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
