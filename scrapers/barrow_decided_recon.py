#!/usr/bin/env python3
"""
PlanFind — Barrow real "Decided" list recon (2026-08-24).

Real, confirmed: the "Decided" column uses the exact same real click
mechanism already proven for "Validated" — a genuine javascript:apex.
navigation.dialog(...) call targeting DECIDEDLIST instead of
VALIDATEDLIST. Never actually seen this list's real content before,
only its trigger link. This recon clicks it directly and saves the
real resulting iframe HTML, same discipline as barrow_iframe_check.py.

Also worth checking directly: whether real decided applications can be
matched back to already-saved 'Validated' records by reference number
alone — if so, this could replace the need for barrow_scraper.py's
honest "no recheck mechanism" limitation entirely, since the current
gap exists specifically because the per-application detail URL is
session-bound and can't be safely stored for later — but matching by
reference during each week's own Decided scan wouldn't need that URL
stored at all.
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

BASE_URL = "https://webapps.barrowbc.gov.uk/webapps/f?p=BARROWPLANNINGHUB:WEEKLYLIST:10007760192139::NO:::"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Barrow real 'Decided' list recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        # Real, direct click on the FIRST real "Decided" View link on
        # the page — same approach already proven for Validated,
        # just targeting the other real column.
        try:
            decided_link = page.locator("td[headers='Decided'] a").first
            await decided_link.click(timeout=8_000)
            print("Clicked the first real 'Decided' View link")
        except Exception as e:
            print(f"⚠ Could not click a real 'Decided' link: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_selector("[role='dialog']", timeout=10_000)
            print("Real dialog element appeared")
        except PlaywrightTimeout:
            print("⚠ No real dialog appeared within 10s")

        await asyncio.sleep(3)

        print(f"\nReal frames on this page right now: {len(page.frames)}")
        found_decided_frame = False
        for i, frame in enumerate(page.frames):
            print(f"\n  Frame {i}: url={frame.url!r}")
            if "DECIDEDLIST" not in frame.url:
                continue
            found_decided_frame = True
            try:
                real_html = await frame.content()
                print(f"    Real content length: {len(real_html)} chars")

                out_html = f"/tmp/barrow_decided_frame.html"
                with open(out_html, "w", encoding="utf-8") as f:
                    f.write(real_html)
                print(f"    Saved: {out_html}")

                frame_text = (await frame.locator("body").inner_text())[:2000]
                print(f"    Real visible text (first 2000 chars): {frame_text!r}")

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(real_html, "html.parser")
                tables = soup.find_all("table")
                print(f"\n    Real <table> elements in this frame: {len(tables)}")
                for j, t in enumerate(tables):
                    rows = t.find_all("tr")
                    print(f"      Table {j}: class={t.get('class')} id={t.get('id')} {len(rows)} rows")
                    if len(rows) > 1:
                        print(f"        Header: {rows[0]}")
                        print(f"        First data row: {rows[1]}")
            except Exception as e:
                print(f"    ⚠ Could not read this frame's content: {e}")

        if not found_decided_frame:
            print("\n⚠ No real frame with 'DECIDEDLIST' in its URL was found")

        out_png = "/tmp/barrow_decided_recon.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
            print(f"\nSaved full-page screenshot: {out_png}")
        except Exception:
            pass

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
