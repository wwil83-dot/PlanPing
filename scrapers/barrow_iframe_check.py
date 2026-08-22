#!/usr/bin/env python3
"""
PlanFind — Barrow modal iframe check (2026-08-22).

Real, confirmed situation: a real dialog element appears (role=
'dialog', with a genuine title "Planning Applications Validated" and
a real "Close" button), even after actively waiting for it plus 3
extra real seconds — but zero tables and zero real application rows
ever show up inside it, across two separate attempts. That consistency
argues against a simple timing issue.

Real, well-known Oracle APEX pattern this hasn't been checked for yet:
modal dialogs are often rendered inside a nested <iframe>, not injected
directly into the main page's DOM. If true, page.content() only ever
sees the empty outer wrapper — the real content lives in a completely
separate document needing its own direct extraction via Playwright's
frame API.
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Barrow modal iframe check\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
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
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        try:
            view_link = page.get_by_text("View (", exact=False).first
            await view_link.click(timeout=10_000)
            print("Clicked the first real 'View (...)' link")
        except Exception as e:
            print(f"⚠ Could not click a real 'View' link: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_selector("[role='dialog']", timeout=10_000)
            print("Real dialog element appeared")
        except PlaywrightTimeout:
            print("⚠ No real dialog appeared")

        await asyncio.sleep(3)

        # REAL, DIRECT CHECK: how many real frames exist on this page
        # right now, and what does each one's own URL/content look
        # like? This is the authoritative way to know whether the
        # modal's real content lives in a separate iframe document.
        frames = page.frames
        print(f"\nReal frames on this page right now: {len(frames)}")
        for i, frame in enumerate(frames):
            print(f"\n  Frame {i}: url={frame.url!r}")
            try:
                frame_html_len = len(await frame.content())
                print(f"    Real content length: {frame_html_len} chars")
                frame_text = (await frame.locator("body").inner_text())[:1500]
                print(f"    Real visible text (first 1500 chars): {frame_text!r}")

                # Real, direct table check within THIS specific frame
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(await frame.content(), "html.parser")
                tables = soup.find_all("table")
                print(f"    Real <table> elements in this frame: {len(tables)}")
                for j, t in enumerate(tables):
                    rows = t.find_all("tr")
                    if len(rows) > 1:
                        print(f"      Table {j}: {len(rows)} rows — header: {rows[0]}")
                        if len(rows) > 1:
                            print(f"      First data row: {rows[1]}")
            except Exception as e:
                print(f"    ⚠ Could not read this frame's content: {e}")

        out_png = "/tmp/barrow_iframe_check.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
            print(f"\nSaved full-page screenshot: {out_png}")
        except Exception:
            pass

        await browser.close()

    print(f"\n{'=' * 70}")
    print("CHECK COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
