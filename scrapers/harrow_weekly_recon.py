#!/usr/bin/env python3
"""
PlanFind — Harrow weekly-list recon (2026-07-24).

Follow-up after civica_recon.py's Harrow attempt hit a wrong path
(returned a genuine Idox "IDX002" error). A user found the REAL, working
page manually: planningsearch.harrow.gov.uk/planning/index.html?fa=
getReceivedWeeklyList — a real, populated table (PL/1954/26, full
addresses, full proposals, a View button per row). Doesn't obviously
match Idox's classic /online-applications/ conventions or Civica
Portal360's knockout.js structure — this captures real HTML/footer
evidence to identify what it actually is before writing any scraper
code, same discipline as every other recon this session.
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

TARGET_URL = "https://planningsearch.harrow.gov.uk/planning/index.html?fa=getReceivedWeeklyList"


async def main():
    print("Harrow weekly-list recon\n")
    print(f"URL: {TARGET_URL}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(2)

        title = await page.title()
        html = await page.content()
        print(f"Real page title: {title!r}")
        print(f"HTML length: {len(html)} chars\n")

        tables = page.locator("table")
        table_count = await tables.count()
        print(f"<table> elements found: {table_count}")

        # Look for a real week-search input, since the screenshot showed
        # a "Week:" text box + Search button
        inputs = page.locator("input")
        input_count = await inputs.count()
        print(f"<input> elements found: {input_count}")
        for i in range(min(input_count, 10)):
            inp = inputs.nth(i)
            try:
                inp_type = await inp.get_attribute("type") or "(text)"
                inp_name = await inp.get_attribute("name") or "(no name)"
                inp_id = await inp.get_attribute("id") or "(no id)"
                print(f"  [{i}] type={inp_type!r} name={inp_name!r} id={inp_id!r}")
            except Exception:
                pass

        # Footer/vendor branding check — generic search for common vendor
        # names, since this doesn't obviously match Idox or Civica
        footer_hits = page.locator("text=/powered by|© \\d{4}|idox|civica|northgate|ocella|agile|objective/i")
        footer_count = await footer_hits.count()
        print(f"\nElements matching vendor/footer-branding text: {footer_count}")
        for i in range(min(footer_count, 5)):
            try:
                text = await footer_hits.nth(i).inner_text()
                print(f"  [{i}] {text!r}")
            except Exception:
                pass

        body_text = await page.locator("body").inner_text()
        snippet = " ".join(body_text.split())[:600]
        print(f"\nVisible body text (first 600 chars):\n  {snippet!r}")

        with open("/tmp/harrow_weekly_recon.html", "w", encoding="utf-8") as f:
            f.write(html)
        try:
            await page.screenshot(path="/tmp/harrow_weekly_recon.png", full_page=True)
            print("\nSaved: /tmp/harrow_weekly_recon.html, /tmp/harrow_weekly_recon.png")
        except Exception as e:
            print(f"\nSaved HTML only (screenshot failed: {e})")

        await browser.close()

    print("\nRecon complete.")


if __name__ == "__main__":
    asyncio.run(main())
