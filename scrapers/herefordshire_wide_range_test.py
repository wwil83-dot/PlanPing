#!/usr/bin/env python3
"""
PlanFind — Herefordshire wide-range URL test (2026-08-28).

Real, confirmed via herefordshire_weekly_list_recon.py: submitting the
real Weekly List search produces a direct, GET-based URL with explicit
date-from/date-to query parameters (a real 7-day range in that test).
Testing whether providing a wider, real 30-day range directly in the
URL also works — if so, this replaces the need to iterate through
individual weeks with a single, real, direct request.
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Herefordshire wide-range URL test\n")

    today = date.today()
    start = today - timedelta(days=30)

    url = (
        "https://www.herefordshire.gov.uk/planning-and-building-control/planning-search"
        f"?search-service=search&search-source=search&search-item="
        f"&date-to={today.isoformat()}&search-term=&date-from={start.isoformat()}"
        f"&status=all&weeklyParishSearch=Weekly+parish+search"
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print(f"Testing real direct URL with a 30-day range:\n{url}\n")

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
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
        except Exception:
            pass

        print(f"Real HTTP status: {response.status if response else None}")
        print(f"Real final URL: {page.url}\n")

        from bs4 import BeautifulSoup
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        print(f"Real <table> elements found: {len(tables)}")
        for i, t in enumerate(tables):
            rows = t.find_all("tr")
            print(f"  Table {i}: {len(rows)} rows")
            if len(rows) > 1:
                print(f"    Header: {rows[0]}")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:1500]
        except Exception:
            pass
        print(f"\nReal visible body text (first 1500 chars): {body_text!r}")

        out_html = "/tmp/herefordshire_wide_range.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\nSaved: {out_html}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
