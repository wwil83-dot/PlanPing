#!/usr/bin/env python3
"""
PlanFind — Stratford real search-flow investigation (2026-08-27).

Real, confirmed gap: no date fields exist anywhere on the landing
page. Clicking "Validated" directly led to "No Results Found" —
likely because it's a fixed-period shortcut (probably "this month"),
not a real date-range search. There's a separate "Search" button never
actually tried yet. Checking what that reveals, and dumping the full
real page structure for direct inspection.
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


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Stratford real search-flow investigation\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto("https://apps.stratford.gov.uk/eplanningv2/Home/MonthlyList",
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
            accept_btn = page.get_by_text("Accept", exact=True)
            if await accept_btn.count() > 0:
                await accept_btn.first.click(timeout=5_000)
                await asyncio.sleep(1)
                print("Accepted real cookie consent\n")
        except Exception:
            pass

        # Real, direct dump of the full page structure BEFORE clicking
        # anything, to understand what "Validated" actually represents
        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        print(f"Real full body text after accepting cookies:\n{body_text}\n")

        print(f"{'=' * 70}")
        print("Now clicking the separate 'Search' button (top-level, not Validated)")
        print("=" * 70)

        try:
            search_btn = page.get_by_role("link", name="Search", exact=True)
            if await search_btn.count() == 0:
                search_btn = page.get_by_text("Search", exact=True)
            await search_btn.first.click(timeout=8_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠ Could not click Search: {e}")
            await browser.close()
            return

        print(f"Real URL after clicking Search: {page.url}")
        title = await page.title()
        print(f"Real page title: {title!r}\n")

        body_text2 = ""
        try:
            body_text2 = (await page.locator("body").inner_text())[:2000]
        except Exception:
            pass
        print(f"Real visible body text (first 2000 chars): {body_text2!r}\n")

        # Real, direct dump of any real input fields on THIS page
        inputs = page.locator("input")
        count = await inputs.count()
        print(f"Real input fields on this page: {count}")
        for i in range(count):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                if itype.lower() != "hidden":
                    print(f"  <input> type={itype!r} name={name!r} id={el_id!r}")
            except Exception:
                pass

        out_html = "/tmp/stratford_search_page.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(await page.content())
        out_png = "/tmp/stratford_search_page.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"\nSaved: {out_html}, {out_png}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("INVESTIGATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
