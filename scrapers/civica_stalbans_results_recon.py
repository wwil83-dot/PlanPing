#!/usr/bin/env python3
"""
PlanFind — Civica Portal360 results-page recon (2026-07-24).

Follow-up to civica_recon.py's homepage recon, which found real,
constructible weekly-list URLs in St Albans' actual HTML — query
parameters (civica.query.decision_dateFrom / decision_dateTo), not a
Salesforce-style form to click through. This is the one piece of real
evidence still missing before writing scraper code: what does the
RESULTS page itself actually look like (a results table? what column/row
structure? what does a real application reference look like?).

Navigates directly to one of the real, confirmed URLs already found in
St Albans' homepage HTML (Last Week's decisions), rather than
constructing a guessed one — same evidence-first principle as
idox_form_recon.py before it.
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

# Real, confirmed URL pulled directly from St Albans' actual homepage
# HTML captured yesterday — not guessed.
TARGET_URL = (
    "https://planningapplications.stalbans.gov.uk/planning/search-applications"
    "?civica.query.decision_dateFrom=12%2F07%2F2026&civica.query.decision_dateTo=18%2F07%2F2026"
)


async def main():
    print("Civica Portal360 results-page recon — St Albans 'Last Week' decisions\n")
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
        await asyncio.sleep(3)  # Portal360 uses knockout.js data-binding —
                                 # give client-side rendering time to finish

        title = await page.title()
        html = await page.content()
        print(f"Real page title: {title!r}")
        print(f"HTML length: {len(html)} chars\n")

        # Generic, exploratory structure detection — same approach as
        # civica_recon.py, no assumptions about the real markup.
        tables = page.locator("table")
        table_count = await tables.count()
        print(f"<table> elements found: {table_count}")

        # Portal360's homepage used knockout.js data-bind attributes
        # (data-bind="foreach: Items") — results are likely rendered the
        # same way, so also check for common list/row patterns beyond
        # plain tables.
        list_items = page.locator("li[class*='result'], div[class*='result'], "
                                   "li[class*='application'], div[class*='application']")
        list_count = await list_items.count()
        print(f"Elements matching result/application-flavoured class names: {list_count}")
        for i in range(min(list_count, 5)):
            try:
                text = await list_items.nth(i).inner_text()
                print(f"  [{i}] {' '.join(text.split())[:200]!r}")
            except Exception:
                pass

        # Look for anything that looks like a real planning reference
        # (e.g. "5/2026/1234" or similar) directly in visible text, since
        # that's the strongest signal real results actually rendered.
        body_text = await page.locator("body").inner_text()
        snippet = " ".join(body_text.split())[:800]
        print(f"\nVisible body text (first 800 chars):\n  {snippet!r}")

        with open("/tmp/civica_stalbans_results.html", "w", encoding="utf-8") as f:
            f.write(html)
        try:
            await page.screenshot(path="/tmp/civica_stalbans_results.png", full_page=True)
            print("\nSaved: /tmp/civica_stalbans_results.html, "
                  "/tmp/civica_stalbans_results.png")
        except Exception as e:
            print(f"\nSaved HTML only (screenshot failed: {e})")

        await browser.close()

    print("\nRecon complete. Download the workflow artifact and read both")
    print("files before writing any scraper extraction logic.")


if __name__ == "__main__":
    asyncio.run(main())
