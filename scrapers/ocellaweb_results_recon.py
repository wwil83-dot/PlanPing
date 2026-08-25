#!/usr/bin/env python3
"""
PlanFind — OcellaWeb real search submission recon (2026-08-25).

Real, confirmed via ocellaweb_family_recon.py: all 4 councils share
identical real field names (reference, location, receivedFrom,
receivedTo, decidedFrom, decidedTo, area, applicant, agent, undecided)
and the page itself explicitly states the expected real date format is
DD-MM-YY (2-digit year) — genuinely different from every other
platform in this project. This submits a real search on Great Yarmouth
(the most well-documented of the 4) and captures the real resulting
page structure, never seen before.
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

URL = "https://planning.great-yarmouth.gov.uk/OcellaWeb/planningSearch"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] OcellaWeb real search submission recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        today = date.today()
        start = today - timedelta(days=60)  # real, wide window to
                                              # maximise the chance of
                                              # real results existing
        # REAL, CONFIRMED format from the page's own text: DD-MM-YY
        from_str = start.strftime("%d-%m-%y")
        to_str = today.strftime("%d-%m-%y")
        print(f"Filling receivedFrom={from_str!r}, receivedTo={to_str!r}")

        try:
            await page.fill("#receivedFrom", from_str, timeout=5_000)
            await page.fill("#receivedTo", to_str, timeout=5_000)
            await page.locator("button:has-text('Search')").first.click(timeout=5_000)
        except Exception as e:
            print(f"⚠ Could not fill/submit search: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        print(f"\nReal URL after search: {page.url}")
        html = await page.content()

        out_html = "/tmp/ocellaweb_results_recon.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/ocellaweb_results_recon.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"Saved: {out_html}, {out_png}")

        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        print(f"\nReal visible body text (first 2500 chars):\n{body_text[:2500]!r}")

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        print(f"\nReal <table> elements found: {len(tables)}")
        for i, t in enumerate(tables):
            rows = t.find_all("tr")
            if len(rows) > 1:
                print(f"  Table {i}: class={t.get('class')} id={t.get('id')} {len(rows)} rows")
                print(f"    Header: {rows[0]}")
                print(f"    First data row: {rows[1]}")

        # Real, direct check for any pagination-suggestive text/links
        pagination_hints = await page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            const hits = [];
            for (const a of links) {
                const t = a.textContent.trim();
                if (/next|page \\d|more/i.test(t) && t.length < 30) {
                    hits.push({text: t, href: a.getAttribute('href')});
                }
            }
            return hits;
        }""")
        print(f"\nReal pagination-suggestive links found: {pagination_hints}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
