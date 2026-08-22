#!/usr/bin/env python3
"""
PlanFind — Westmorland and Furness recon, round 3 (2026-08-22).

Two real, remaining gaps from round 2:
1. Barrow's modal genuinely opened (confirmed: real title "Planning
   Applications Validated" + a real "Close" button appeared) but its
   actual data rows never showed up in the captured content — a real
   AJAX-timing issue, same category already fixed twice this session
   (statmap's East Staffordshire, agileapplications' "Show more"
   link). Actively waiting for real row content this time, not a
   blind fixed sleep.
2. Eden/South Lakeland's Advanced Search form was seen, but never
   actually submitted — no real results-page structure known yet.
   Filling DateReceivedFrom/To with a real 30-day window and
   submitting for real this time.
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


async def recon_eden_south_lakeland_search(browser):
    print(f"\n{'=' * 70}")
    print("ROUND 3: Eden/South Lakeland — real search submission")
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

    today = date.today()
    start = today - timedelta(days=30)
    date_from_str = start.strftime("%d/%m/%Y")
    date_to_str = today.strftime("%d/%m/%Y")

    try:
        await page.fill("#DateReceivedFrom", date_from_str, timeout=5_000)
        print(f"  Filled #DateReceivedFrom with {date_from_str!r}")
    except Exception as e:
        print(f"  ⚠ Could not fill DateReceivedFrom: {e}")

    try:
        await page.fill("#DateReceivedTo", date_to_str, timeout=5_000)
        print(f"  Filled #DateReceivedTo with {date_to_str!r}")
    except Exception as e:
        print(f"  ⚠ Could not fill DateReceivedTo: {e}")

    # Real search button — not yet confirmed by name/id, trying common
    # real patterns defensively
    clicked = False
    for selector in ["button:has-text('Search')", "input[type='submit']",
                      "#submitBtn", "button[type='submit']"]:
        try:
            btn = page.locator(selector)
            if await btn.count() > 0 and await btn.first.is_visible(timeout=1000):
                await btn.first.click(timeout=5_000)
                clicked = True
                print(f"  Clicked real search button via {selector!r}")
                break
        except Exception:
            continue

    if not clicked:
        print("  ⚠ Could not find/click a real search button")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1.5)

    title = await page.title()
    print(f"  Real page title after search: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = "/tmp/wandf_r3_esl_results.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = "/tmp/wandf_r3_esl_results.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:2000]
    except Exception:
        pass
    print(f"\n  Real visible body text after search (first 2000 chars): {body_text!r}")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"\n  Real <table> elements found: {len(tables)}")
    for i, t in enumerate(tables):
        rows = t.find_all("tr")
        if len(rows) > 1:
            print(f"  Table {i}: {len(rows)} rows — header: {rows[0]}")

    await context.close()


async def recon_barrow_modal_fixed(browser):
    print(f"\n{'=' * 70}")
    print("ROUND 3: Barrow — real modal, actively waiting for real content")
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

    try:
        view_link = page.get_by_text("View (", exact=False).first
        await view_link.click(timeout=10_000)
        print("  Clicked the first real 'View (...)' link")
    except Exception as e:
        print(f"  ⚠ Could not click a real 'View' link: {e}")
        await context.close()
        return

    # REAL FIX — actively wait for real dialog content, not a blind
    # fixed sleep. APEX modals typically render inside a real element
    # with role="dialog" — waiting for that, then giving its own
    # internal AJAX-loaded table extra real time on top.
    try:
        await page.wait_for_selector("[role='dialog']", timeout=10_000)
        print("  Real dialog element appeared")
    except PlaywrightTimeout:
        print("  ⚠ No real [role='dialog'] element appeared within 10s")

    await asyncio.sleep(3)  # real, deliberate extra wait for the
                              # dialog's own internal AJAX content

    html = await page.content()
    out_html = "/tmp/wandf_r3_barrow_modal_fixed.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = "/tmp/wandf_r3_barrow_modal_fixed.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:2500]
    except Exception:
        pass
    print(f"\n  Real visible body text after real wait (first 2500 chars): {body_text!r}")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    dialog = soup.find(attrs={"role": "dialog"})
    if dialog:
        tables_in_dialog = dialog.find_all("table")
        print(f"\n  Real <table> elements found INSIDE the real dialog: {len(tables_in_dialog)}")
        for i, t in enumerate(tables_in_dialog):
            rows = t.find_all("tr")
            print(f"  Dialog table {i}: {len(rows)} rows")
            if rows:
                print(f"    First row: {rows[0]}")
                if len(rows) > 1:
                    print(f"    Second row: {rows[1]}")
    else:
        print("\n  ⚠ No real [role='dialog'] element found in the saved HTML at all")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Westmorland and Furness recon "
          f"round 3\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await recon_eden_south_lakeland_search(browser)
        await recon_barrow_modal_fixed(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON ROUND 3 COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
