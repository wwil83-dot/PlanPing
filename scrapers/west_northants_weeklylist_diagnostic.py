#!/usr/bin/env python3
"""
West Northamptonshire Weekly List diagnostic (2026-09-30).

Real, confirmed context: West Northants was previously PARKED as
"confirmed not Idox — planning-register.co.uk vendor, reCAPTCHA
blocked" (see idox_councils.py's own comment). That's the same vendor
already confirmed working for Vale of White Horse and South
Oxfordshire (vowh_soxon_weeklylist_scraper.py), where the main search
form was separately confirmed broken/reCAPTCHA-affected but the
Weekly List CSV download flow worked cleanly. This checks whether the
same split applies here: is THIS specific page (wnc.planning-
register.co.uk/Planning/WeeklyList/) genuinely reCAPTCHA-free, and
does its "Applications Registered Valid During Period" link lead to a
CSV download (matching VoWH/South Oxfordshire's confirmed
.getWeeklyListCSV button) or something else entirely.
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

WEEKLY_LIST_URL = "https://wnc.planning-register.co.uk/Planning/WeeklyList/"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        await page.goto(WEEKLY_LIST_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"Real page title: {await page.title()}")
        print(f"Real page URL after load: {page.url}")

        # REAL FIX — confirmed via the actual run: this redirected to a
        # Disclaimer page first (same real pattern already proven for
        # Vale of Glamorgan on this same planning-register.co.uk
        # vendor), which is why zero period links were found — the
        # weekly list content was never actually reached. Accepting it
        # and re-navigating to the real target URL.
        if "Disclaimer" in page.url:
            print("\nReal Disclaimer page detected — looking for an accept/continue control")
            accept_candidates = [
                "button:has-text('Accept')",
                "a:has-text('Accept')",
                "button:has-text('Continue')",
                "a:has-text('Continue')",
                "input[type='submit']",
            ]
            clicked = False
            for sel in accept_candidates:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    print(f"Real accept control found: {sel!r}")
                    await loc.first.click(timeout=5_000)
                    clicked = True
                    break
            if not clicked:
                print("⚠ No real accept/continue control found on the Disclaimer page")
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                print(f"Real page title after accepting: {await page.title()}")
                print(f"Real page URL after accepting: {page.url}")

        body_text = await page.locator("body").inner_text()
        recaptcha_signatures = ["recaptcha", "i'm not a robot", "captcha"]
        found_recaptcha = [s for s in recaptcha_signatures if s in body_text.lower()]
        print(f"\nReal reCAPTCHA signatures found on this page: {found_recaptcha or 'NONE'}")

        first_period_link = page.locator("a:has-text('Applications Registered Valid During Period')").first
        count = await first_period_link.count()
        print(f"\nReal 'Applications Registered Valid During Period' links found: {count}")

        if count > 0:
            href = await first_period_link.get_attribute("href")
            classes = await first_period_link.get_attribute("class")
            data_date = await first_period_link.get_attribute("data-date")
            print(f"Real href: {href!r}")
            print(f"Real class: {classes!r}")
            print(f"Real data-date attribute: {data_date!r}")

            try:
                async with page.expect_download(timeout=15_000) as download_info:
                    await first_period_link.click(timeout=5_000)
                download = await download_info.value
                print(f"\nReal download triggered: {download.suggested_filename}")
            except PlaywrightTimeout:
                print(f"\nNo real download triggered within 15s — checking if it "
                      f"navigated instead")
                print(f"Real URL after click: {page.url}")
                new_body = await page.locator("body").inner_text()
                new_recaptcha = [s for s in recaptcha_signatures if s in new_body.lower()]
                print(f"Real reCAPTCHA signatures found after click: {new_recaptcha or 'NONE'}")
                print(f"Real body text after click (first 1000 chars): {new_body[:1000]!r}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
