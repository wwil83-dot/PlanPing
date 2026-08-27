#!/usr/bin/env python3
"""
PlanFind — Stratford Advanced Search real submission test (2026-08-27).

Real, confirmed field ids from stratford_advanced_search_recon.py:
dateAppValidFrom / dateAppValidTo, genuine native type="date" inputs,
appearing twice in the DOM (likely a responsive mobile/desktop
duplicate) — using .first defensively. Never actually submitted a real
search here before — this is the first direct look at what the real
results page looks like.
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Stratford Advanced Search real submission test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto("https://apps.stratford.gov.uk/eplanningv2/Home/AdvancedSearch",
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

        today = date.today()
        start = today - timedelta(days=30)

        try:
            for field_id, value in [("dateAppValidFrom", start.isoformat()),
                                      ("dateAppValidTo", today.isoformat())]:
                await page.evaluate(
                    """([id, val]) => {
                        const els = document.querySelectorAll('#' + id);
                        els.forEach(el => {
                            el.value = val;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        });
                    }""",
                    [field_id, value],
                )
            print(f"Set real dates via JS (all matching elements): {start.isoformat()} to {today.isoformat()}\n")
        except Exception as e:
            print(f"⚠ Could not set date fields: {e}")
            await browser.close()
            return

        # Real, direct search for a Search button on this page
        try:
            search_btn = page.locator("button:has-text('Search')")
            count = await search_btn.count()
            print(f"Real 'Search' buttons found: {count}")
            if count == 0:
                search_btn = page.locator("input[type='submit']")
                count = await search_btn.count()
                print(f"Real submit inputs found instead: {count}")
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

        out_html = "/tmp/stratford_real_results.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/stratford_real_results.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"\nSaved: {out_html}, {out_png}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
