#!/usr/bin/env python3
"""
PlanFind — Herefordshire real results page recon (2026-08-27).

Real, confirmed from earlier round 2 recon: filling the real date
fields and observing the page revealed an autocomplete/typeahead
dropdown (a real <select class="Search-List">) showing only a small
sample of applications, with a real "Show all 366 applications that
match the keyword ''" option — the genuine full result set was never
actually reached. Testing whether clicking the real "Search" button
scoped to the date fields bypasses this autocomplete entirely and
lands on the actual full results page directly.
"""
import asyncio
from datetime import date, timedelta, datetime, timezone

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


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Herefordshire real results page recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto("https://www.herefordshire.gov.uk/planning-and-building-control/planning-search",
                             wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        try:
            accept_btn = page.get_by_text("Accept cookies", exact=True)
            if await accept_btn.count() > 0:
                await accept_btn.first.click(timeout=5_000)
                await asyncio.sleep(1)
                print("Accepted real cookies\n")
        except Exception:
            pass

        today = date.today()
        start = today - timedelta(days=30)

        try:
            await page.locator("#date-from").first.fill(start.strftime("%Y-%m-%d"), timeout=5_000)
            await page.locator("#date-to").first.fill(today.strftime("%Y-%m-%d"), timeout=5_000)
            print(f"Filled real dates via native .fill(): {start.isoformat()} to {today.isoformat()}\n")
        except Exception as e:
            print(f"⚠ Could not fill date fields: {e}")
            await browser.close()
            return

        # Real, scoped Search button — same proven pattern as every
        # other platform: target a button inside the real form
        # containing the date fields just filled
        try:
            form_with_dates = page.locator("form").filter(has=page.locator("#date-from"))
            search_btn = form_with_dates.locator("button:has-text('Search')")
            count = await search_btn.count()
            print(f"Real 'Search' buttons scoped to the date-containing form: {count}")
            await search_btn.first.click(timeout=8_000)
        except Exception as e:
            print(f"⚠ Could not click Search: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1.5)

        print(f"\nReal URL after search: {page.url}")
        title = await page.title()
        print(f"Real page title: {title!r}\n")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:2500]
        except Exception:
            pass
        print(f"Real visible body text (first 2500 chars): {body_text!r}\n")

        from bs4 import BeautifulSoup
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        print(f"Real <table> elements found: {len(tables)}")
        for i, t in enumerate(tables):
            rows = t.find_all("tr")
            if len(rows) > 1:
                print(f"  Table {i}: {len(rows)} rows — header: {rows[0]}")

        out_html = "/tmp/herefordshire_real_results.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/herefordshire_real_results.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"\nSaved: {out_html}, {out_png}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
