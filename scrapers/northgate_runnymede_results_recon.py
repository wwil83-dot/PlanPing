#!/usr/bin/env python3
"""
PlanFind — Northgate PlanningExplorer results-page recon (2026-07-24).

Follow-up to northgate_recon.py's homepage recon, which found Runnymede's
real, working ASP.NET WebForms search form (VIEWSTATE/EVENTVALIDATION —
classic postback pattern, not a URL-constructible search like Civica
Portal360). This fills the REAL confirmed fields (rbRange radio button +
dateStart/dateEnd text inputs) and clicks the real Search button
(csbtnSearch), then captures whatever the results page actually looks
like — the one piece of evidence still missing before writing any
scraper extraction logic, same principle as civica_stalbans_results_
recon.py before it.

Since this is a genuine ASP.NET postback (not a constructible URL like
Civica), this uses real Playwright form-fill + click, letting the
browser handle VIEWSTATE submission naturally, same as a real user
would.
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

TARGET_URL = "https://planning.runnymede.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx"


async def main():
    print("Northgate PlanningExplorer results-page recon — Runnymede\n")
    print(f"URL: {TARGET_URL}\n")

    today = date.today()
    date_from = today - timedelta(days=14)
    print(f"Date range: {date_from.strftime('%d/%m/%Y')} to {today.strftime('%d/%m/%Y')}\n")

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
        await asyncio.sleep(1)

        # Real, confirmed field IDs from the homepage recon
        try:
            await page.check("#rbRange", timeout=5_000)
            print("Checked: rbRange radio")
        except Exception as e:
            print(f"⚠ Couldn't check rbRange: {e}")

        try:
            await page.fill("#dateStart", date_from.strftime("%d/%m/%Y"), timeout=5_000)
            print(f"Filled dateStart: {date_from.strftime('%d/%m/%Y')}")
        except Exception as e:
            print(f"⚠ Couldn't fill dateStart: {e}")

        try:
            await page.fill("#dateEnd", today.strftime("%d/%m/%Y"), timeout=5_000)
            print(f"Filled dateEnd: {today.strftime('%d/%m/%Y')}")
        except Exception as e:
            print(f"⚠ Couldn't fill dateEnd: {e}")

        try:
            await page.click("#csbtnSearch", timeout=5_000)
            print("Clicked: csbtnSearch\n")
        except Exception as e:
            print(f"⚠ Couldn't click csbtnSearch: {e}\n")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            print("⚠ Results timeout after real submit\n")
        await asyncio.sleep(3)

        title = await page.title()
        html = await page.content()
        print(f"Real page title after submit: {title!r}")
        print(f"HTML length: {len(html)} chars\n")

        tables = page.locator("table")
        table_count = await tables.count()
        print(f"<table> elements found: {table_count}")

        # ASP.NET GridView convention — rows typically have alternating
        # CSS classes and an id containing "GridView" or "gv"
        grid_rows = page.locator("tr[class*='row'], tr[id*='GridView'], tr[id*='gv']")
        grid_count = await grid_rows.count()
        print(f"Elements matching GridView-flavoured row selectors: {grid_count}")

        # Generic: any element containing a real-looking planning
        # reference pattern in its text
        try:
            body_text = await page.locator("body").inner_text()
            snippet = " ".join(body_text.split())[:1000]
            print(f"\nVisible body text (first 1000 chars):\n  {snippet!r}")
        except Exception as e:
            print(f"\n(couldn't extract body text: {e})")

        with open("/tmp/northgate_runnymede_results.html", "w", encoding="utf-8") as f:
            f.write(html)
        try:
            await page.screenshot(path="/tmp/northgate_runnymede_results.png", full_page=True)
            print("\nSaved: /tmp/northgate_runnymede_results.html, "
                  "/tmp/northgate_runnymede_results.png")
        except Exception as e:
            print(f"\nSaved HTML only (screenshot failed: {e})")

        # FOLLOW-UP (2026-07-24): a user reported broken detail links for
        # specific applications (RU.26/0920, 0919, etc.) that don't
        # appear on page 1 of a fresh search — they're older/lower-
        # numbered, so almost certainly only reachable via pagination.
        # Page 1's raw href structure has already been confirmed/tested,
        # but page 2+ hasn't been inspected directly — capturing it now
        # rather than assume it's identical.
        next_link = page.locator("a:has(img[alt='Go to next page '])")
        if await next_link.count() > 0:
            try:
                await next_link.first.click(timeout=5_000)
                await asyncio.sleep(1)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(2)

                page2_title = await page.title()
                page2_html = await page.content()
                print(f"\nPage 2 title: {page2_title!r}")
                print(f"Page 2 HTML length: {len(page2_html)} chars")

                with open("/tmp/northgate_runnymede_results_page2.html", "w", encoding="utf-8") as f:
                    f.write(page2_html)
                try:
                    await page.screenshot(path="/tmp/northgate_runnymede_results_page2.png", full_page=True)
                    print("Saved: /tmp/northgate_runnymede_results_page2.html, "
                          "/tmp/northgate_runnymede_results_page2.png")
                except Exception as e:
                    print(f"Saved page 2 HTML only (screenshot failed: {e})")
            except Exception as e:
                print(f"\n⚠ Couldn't navigate to page 2: {e}")
        else:
            print("\n⚠ No 'next page' link found — couldn't capture page 2")

        await browser.close()

    print("\nRecon complete. Download the workflow artifact and read both")
    print("files before writing any scraper extraction logic.")


if __name__ == "__main__":
    asyncio.run(main())
