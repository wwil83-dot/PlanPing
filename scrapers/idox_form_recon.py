#!/usr/bin/env python3
"""
PlanFind — Idox form-element reconnaissance.

PURPOSE: the idox_month_test.py diagnostic conclusively proved that NONE
of the 4 candidate CSS selectors idox_scraper.py uses to find the
month-selection dropdown match anything on 3 real councils' pages,
spanning 2 different underlying server setups. Rather than guess a 5th
selector blind, this captures direct HTML evidence of every <select> and
relevant <input> element actually present on a real monthly-list page —
the same "get real evidence before fixing" principle that cracked every
Arcus mystery this session.

Prints (to the console log, so no artifact download needed for the quick
answer) every <select> element's id/name/class plus its option
values/labels, and every radio <input> whose name/value/id suggests it's
part of the date-type selector (dateReceived/dateValidated/etc — the
OTHER piece of form interaction in _scrape_month that hasn't been
confirmed broken, but is worth double-checking given how wrong the
month-dropdown assumptions turned out to be).

Also saves full HTML + a screenshot as a workflow artifact, in case the
console summary isn't enough to figure out the real fix.

Run via GitHub Actions workflow_dispatch (see scrape.yml).
"""
import asyncio
from playwright.async_api import async_playwright

TARGET_URL = (
    "https://planning.stockport.gov.uk/PlanningData-live"
    "/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"
)
TARGET_NAME = "Stockport Metropolitan Borough Council"

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
    print(f"Idox form-element recon — {TARGET_NAME}")
    print(f"Target URL: {TARGET_URL}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(3)
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        title = await page.title()
        print(f"Page title: '{title}'\n")

        # --- Every <select> element on the page, with full attributes and options ---
        selects = page.locator("select")
        select_count = await selects.count()
        print(f"=== Found {select_count} <select> element(s) on this page ===\n")

        for i in range(select_count):
            sel = selects.nth(i)
            try:
                sel_id = await sel.get_attribute("id") or "(no id)"
                sel_name = await sel.get_attribute("name") or "(no name)"
                sel_class = await sel.get_attribute("class") or "(no class)"
                print(f"  Select #{i}: id={sel_id!r}, name={sel_name!r}, class={sel_class!r}")

                options = sel.locator("option")
                opt_count = await options.count()
                print(f"    Options ({opt_count}):")
                for j in range(min(opt_count, 15)):
                    opt = options.nth(j)
                    opt_value = await opt.get_attribute("value") or ""
                    opt_text = await opt.inner_text()
                    print(f"      [{j}] value={opt_value!r} text={opt_text!r}")
                print()
            except Exception as e:
                print(f"  Select #{i}: (error reading attributes: {e})\n")

        # --- Every radio input whose name/id/value hints at date-type selection ---
        print("=== Radio inputs whose name/id/value mention 'date' or 'receiv'/'valid'/'regist' ===\n")
        radios = page.locator("input[type='radio']")
        radio_count = await radios.count()
        print(f"Total radio inputs on page: {radio_count}\n")
        for i in range(radio_count):
            r = radios.nth(i)
            try:
                r_id = await r.get_attribute("id") or ""
                r_name = await r.get_attribute("name") or ""
                r_value = await r.get_attribute("value") or ""
                combined = f"{r_id} {r_name} {r_value}".lower()
                if any(kw in combined for kw in ("date", "receiv", "valid", "regist")):
                    print(f"  Radio #{i}: id={r_id!r}, name={r_name!r}, value={r_value!r}")
            except Exception:
                continue

        # --- Save full HTML + screenshot regardless, as a backup artifact ---
        html = await page.content()
        with open("/tmp/idox_form_recon.html", "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path="/tmp/idox_form_recon.png", full_page=True)
        print(f"\nFull HTML length: {len(html)} chars")
        print("Saved /tmp/idox_form_recon.html and /tmp/idox_form_recon.png as backup artifacts")

        await browser.close()

    print("\n=== Recon complete ===")


if __name__ == "__main__":
    asyncio.run(main())
