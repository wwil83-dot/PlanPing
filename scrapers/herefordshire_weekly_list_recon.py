#!/usr/bin/env python3
"""
PlanFind — Herefordshire Weekly List recon (2026-08-28).

Real, valuable pivot: the "Search applications" tab's date-range
search consistently leads to an autocomplete dropdown rather than real
results (confirmed across multiple recon rounds) — but the person
running this project found a completely different, much simpler real
tab: "Weekly list", producing a real, clean table (Application number |
Site address | Description | Type | Status | Comments by), directly
confirmed via their own screenshots. Getting real, computer-readable
HTML for this tab's actual form fields before building anything,
rather than work from screenshots alone.
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Herefordshire Weekly List recon\n")

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

        # Real, confirmed via the user's own screenshot: a "Weekly
        # list" tab exists as a real, separate option from "Search
        # applications"
        try:
            weekly_tab = page.get_by_text("Weekly list", exact=True)
            count = await weekly_tab.count()
            print(f"Real 'Weekly list' tab elements found: {count}")
            await weekly_tab.first.click(timeout=8_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠ Could not click Weekly list tab: {e}")
            await browser.close()
            return

        print(f"Real URL after clicking Weekly list: {page.url}\n")

        # Real, direct dump of every real form field on this tab
        inputs = page.locator("input")
        count = await inputs.count()
        print(f"Real input fields: {count}")
        for i in range(count):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                value = await el.get_attribute("value") or ""
                if itype.lower() != "hidden":
                    print(f"  <input> type={itype!r} name={name!r} id={el_id!r} value={value!r}")
            except Exception:
                pass

        buttons = page.locator("button, input[type='submit']")
        bcount = await buttons.count()
        print(f"\nReal buttons: {bcount}")
        for i in range(bcount):
            el = buttons.nth(i)
            try:
                text = (await el.inner_text()) or (await el.get_attribute("value")) or ""
                if text.strip():
                    print(f"  <button> text={text.strip()!r}")
            except Exception:
                pass

        # REAL FIX — confirmed via direct timeout evidence: the
        # generic input[type='date'] selector grabbed the OTHER tab's
        # hidden field (#date-from), since both tabs' HTML coexists on
        # the same page (CSS-toggled visibility, not separate
        # navigation). Targeting the specific real field id confirmed
        # for THIS tab. Also real, confirmed: only ONE date field
        # exists here (a single week-anchor date), not a from/to pair
        # — makes sense, a weekly list only needs one anchor date.
        today = date.today()
        test_date = today - timedelta(days=3)

        try:
            await page.locator("#parish-weekly-search-datefrom").fill(test_date.isoformat(), timeout=5_000)
            print(f"Filled real week date via the correct field id: {test_date.isoformat()}")
        except Exception as e:
            print(f"⚠ Could not fill week date: {e}")
            await browser.close()
            return

        try:
            # REAL FIX — confirmed via direct evidence: an unscoped
            # "Search" click hit the site's generic header search
            # instead (real resulting URL: herefordshire.gov.uk/
            # search?q=) — same category of hijack bug already fixed
            # for Cherwell earlier in this project. Scoping to a real
            # form containing the just-filled field.
            form_with_date = page.locator("form").filter(has=page.locator("#parish-weekly-search-datefrom"))
            search_btn = form_with_date.locator("button:has-text('Search')")
            count = await search_btn.count()
            print(f"Real scoped 'Search' buttons found: {count}")
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

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:2000]
        except Exception:
            pass
        print(f"\nReal visible body text (first 2000 chars): {body_text!r}\n")

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

        out_html = "/tmp/herefordshire_weekly_list.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/herefordshire_weekly_list.png"
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
