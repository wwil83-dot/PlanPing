#!/usr/bin/env python3
"""
PlanFind — Westmorland and Furness recon, round 2 (2026-08-22).

Round 1 found two real, open questions:
1. Eden/South Lakeland's Quick Search landing page has no date fields
   at all (just reference number / location text) — real date-range
   fields, if any, must live on the separate /Search/Advanced page,
   never actually visited yet. Worth noting directly: this exact
   "/Search/Advanced" URL path matches Cherwell, North Warwickshire,
   Wychavon, and Malvern Hills from an earlier council-search batch —
   this may be the SAME shared platform, not a unique one-off.
2. Barrow's "View (N)" links are real javascript:apex.navigation.
   dialog(...) calls, not plain page links — genuinely open a JS-
   driven modal, not a normal navigation. Never actually clicked one
   yet to see what's inside.
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


async def dump_form_fields(page, prefix="  "):
    print(f"\n{prefix}Real form fields found:")
    try:
        inputs = page.locator("input")
        count = await inputs.count()
        for i in range(count):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                placeholder = await el.get_attribute("placeholder") or ""
                if itype.lower() not in ("hidden",):
                    print(f"{prefix}  <input> type={itype!r} name={name!r} id={el_id!r} placeholder={placeholder!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"{prefix}  ⚠ input dump error: {e}")

    try:
        selects = page.locator("select")
        count = await selects.count()
        for i in range(count):
            el = selects.nth(i)
            try:
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                opts = await el.locator("option").all_text_contents()
                print(f"{prefix}  <select> name={name!r} id={el_id!r} options={opts[:10]}")
            except Exception:
                pass
    except Exception as e:
        print(f"{prefix}  ⚠ select dump error: {e}")


async def recon_eden_south_lakeland(browser):
    print(f"\n{'=' * 70}")
    print("ROUND 2: Eden/South Lakeland Advanced Search")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://planningregister.westmorlandandfurness.gov.uk/Search/Advanced",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = "/tmp/wandf_r2_eden_south_lakeland_advanced.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = "/tmp/wandf_r2_eden_south_lakeland_advanced.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_form_fields(page)

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1500]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1500 chars): {body_text!r}")

    await context.close()


async def recon_barrow(browser):
    print(f"\n{'=' * 70}")
    print("ROUND 2: Barrow — clicking a real 'View' link to see the modal")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(
            "https://webapps.barrowbc.gov.uk/webapps/f?p=BARROWPLANNINGHUB:WEEKLYLIST:10007760192139::NO:::",
            wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    # Real, confirmed link text from round 1 — click the first real
    # "View" link found (a Validated list) and see what actually opens
    try:
        view_link = page.get_by_text("View (", exact=False).first
        await view_link.click(timeout=10_000)
        print("  Clicked the first real 'View (...)' link")
    except Exception as e:
        print(f"  ⚠ Could not click a real 'View' link: {e}")
        await context.close()
        return

    await asyncio.sleep(2)  # real, deliberate pause for the APEX modal
                              # dialog to finish opening/rendering

    # Real, direct check for whatever dialog/modal structure APEX uses
    html = await page.content()
    out_html = "/tmp/wandf_r2_barrow_modal.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = "/tmp/wandf_r2_barrow_modal.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    print(f"  Real URL after clicking (APEX dialogs often don't change the URL): {page.url}")

    await dump_form_fields(page)

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:2000]
    except Exception:
        pass
    print(f"\n  Real visible body text after click (first 2000 chars): {body_text!r}")

    # Real, direct check for a table inside whatever real dialog
    # structure exists
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"\n  Real <table> elements found after click: {len(tables)}")
    if tables:
        rows = tables[-1].find_all("tr")  # last table often the real
                                            # dialog content, not page chrome
        print(f"  Last table has {len(rows)} rows")
        if rows:
            print(f"  Header/first row: {rows[0]}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Westmorland and Furness recon "
          f"round 2\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await recon_eden_south_lakeland(browser)
        await recon_barrow(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON ROUND 2 COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
