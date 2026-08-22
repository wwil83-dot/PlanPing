#!/usr/bin/env python3
"""
PlanFind — Eden/South Lakeland pagination test (2026-08-22).

Real, confirmed gap: a real search returned 114 total results, only 10
shown, and a full, complete real HTML capture confirmed ZERO
pagination controls anywhere on the page — no next link, no page
numbers, nothing. Testing the most common real pattern for this kind
of ASP.NET-flavored site directly: a query-string page parameter,
even without any visible on-page control for it.
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


async def submit_real_search(page):
    await page.goto("https://planningregister.westmorlandandfurness.gov.uk/Search/Advanced",
                     wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    today = date.today()
    start = today - timedelta(days=30)
    await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
    await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)
    await page.locator("button:has-text('Search')").first.click(timeout=5_000)

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Eden/South Lakeland pagination test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print("Submitting the real search (page 1)...")
        await submit_real_search(page)
        real_results_url = page.url
        print(f"Real page 1 URL: {real_results_url}\n")

        from bs4 import BeautifulSoup

        def count_rows(html):
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table")
            if not table:
                return 0, []
            rows = table.find_all("tr")
            refs = []
            for r in rows[1:]:
                cells = r.find_all("td")
                if cells:
                    a = cells[0].find("a")
                    if a:
                        refs.append(a.get_text(strip=True))
            return len(rows) - 1, refs

        html1 = await page.content()
        count1, refs1 = count_rows(html1)
        print(f"Page 1: {count1} rows, refs: {refs1[:3]}...\n")

        # Try common real query-param patterns directly
        test_urls = [
            f"{real_results_url}&page=2" if "?" in real_results_url else f"{real_results_url}?page=2",
            f"{real_results_url}&Page=2" if "?" in real_results_url else f"{real_results_url}?Page=2",
            f"{real_results_url}&p=2" if "?" in real_results_url else f"{real_results_url}?p=2",
            f"{real_results_url}&pageNumber=2" if "?" in real_results_url else f"{real_results_url}?pageNumber=2",
        ]

        for test_url in test_urls:
            print(f"Testing: {test_url}")
            try:
                await page.goto(test_url, wait_until="domcontentloaded", timeout=20_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                html2 = await page.content()
                count2, refs2 = count_rows(html2)
                changed = refs2 != refs1
                print(f"  Rows: {count2}, refs: {refs2[:3]}..., DIFFERENT from page 1: {changed}")
            except Exception as e:
                print(f"  ⚠ Error: {e}")
            print()

        await context.close()
        await browser.close()

    print(f"{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
