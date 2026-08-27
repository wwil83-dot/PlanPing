#!/usr/bin/env python3
"""
PlanFind — Stratford Advanced Search recon (2026-08-27).

Real, confirmed: the separate "Search" button leads to a genuinely
different real system with an "Advanced Search" link, described as
searching by "reference, postcode, or first line of address" — much
more likely to have a familiar date-range search than the Monthly
List's clunkier Parish+Month dropdown approach (which DOES work, now
fully understood, but requires iterating through individual month
selections rather than a clean from/to range).
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Stratford Advanced Search recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto("https://apps.stratford.gov.uk/eplanningv2",
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
        except Exception:
            pass

        try:
            adv_link = page.get_by_text("Advanced Search", exact=True)
            await adv_link.first.click(timeout=8_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠ Could not click Advanced Search: {e}")
            await browser.close()
            return

        print(f"Real URL after clicking Advanced Search: {page.url}")
        title = await page.title()
        print(f"Real page title: {title!r}\n")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:2500]
        except Exception:
            pass
        print(f"Real visible body text (first 2500 chars): {body_text!r}\n")

        inputs = page.locator("input")
        count = await inputs.count()
        print(f"Real input fields on this page: {count}")
        for i in range(count):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                placeholder = await el.get_attribute("placeholder") or ""
                if itype.lower() != "hidden":
                    print(f"  <input> type={itype!r} name={name!r} id={el_id!r} placeholder={placeholder!r}")
            except Exception:
                pass

        selects = page.locator("select")
        scount = await selects.count()
        print(f"\nReal select dropdowns: {scount}")
        for i in range(scount):
            el = selects.nth(i)
            name = await el.get_attribute("name") or ""
            el_id = await el.get_attribute("id") or ""
            print(f"  <select> name={name!r} id={el_id!r}")

        out_html = "/tmp/stratford_advanced_search.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(await page.content())
        out_png = "/tmp/stratford_advanced_search.png"
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
