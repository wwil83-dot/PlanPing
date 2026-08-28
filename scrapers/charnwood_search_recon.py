#!/usr/bin/env python3
"""
PlanFind — Charnwood (Assure) real search submission recon (2026-08-28).

Real, confirmed via charnwood_assure_recon.py: a genuine preset date-
range selector exists ("Past 24 hours / Past week / Past month / Past
year / Received any time / Custom date range"), plus real "SearchFor"
radio options (Planning applications / Planning appeals / Planning
enforcements / TPOs / Works to trees). Selecting "Planning
applications" and "Past month", then submitting, to see the real
results structure — never actually seen before.
"""
import asyncio
from datetime import datetime, timezone

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

URL = "https://planningexplorer.charnwood.gov.uk/Assure/ES/Presentation/Planning/OnLinePlanning/OnlinePlanningSearch"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Charnwood real search submission recon\n")

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

        try:
            await page.locator("#PlanningApplications").check(timeout=5_000)
            print("Checked real 'Planning applications' radio")
        except Exception as e:
            print(f"⚠ Could not check Planning applications: {e}")

        try:
            past_month = page.get_by_text("Past month", exact=True)
            count = await past_month.count()
            print(f"Real 'Past month' elements found: {count}")
            await past_month.first.click(timeout=5_000)
            print("Clicked real 'Past month'")
        except Exception as e:
            print(f"⚠ Could not click Past month: {e}")

        try:
            # REAL FIX — confirmed via direct HTML inspection: the
            # original "button:has-text('Search')" substring match
            # grabbed the wrong one of 3 real Search-related buttons
            # (likely "Advanced search", appearing first in DOM order)
            # — the REAL basic-search button has a confirmed id
            # (#ancBasicSearch) with a direct onclick handler
            # (OnlinePlanningBasicSearch.SubmitForm()), the actual
            # correct target for the filter/preset search just
            # configured.
            search_btn = page.locator("#ancBasicSearch")
            count = await search_btn.count()
            print(f"Real #ancBasicSearch buttons found: {count}")
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
                print(f"  First data row: {rows[1]}")

        out_html = "/tmp/charnwood_search_results.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/charnwood_search_results.png"
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
