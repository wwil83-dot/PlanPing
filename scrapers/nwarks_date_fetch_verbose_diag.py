#!/usr/bin/env python3
"""
PlanFind — verbose diagnostic for North Warwickshire's real date fetch
(2026-08-25).

Real, genuinely inconclusive result from test_nwarks_date_fetch_direct.py:
an empty result could mean either the disclaimer fix isn't working, or
this specific reference just has no parseable date. fetch_received_dates()
itself has no visibility into which — replicating its exact real steps
here with full diagnostic output at each stage, so the actual cause is
directly visible rather than guessed at.
"""
import asyncio
import re
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

REAL_URL = "https://planning.northwarks.gov.uk/Planning/Display?applicationNumber=2026%2F0661%2FLAWP"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Verbose North Warwickshire date fetch diagnostic\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print(f"Step 1 — navigating to: {REAL_URL}")
        try:
            await page.goto(REAL_URL, wait_until="domcontentloaded", timeout=30_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        print(f"Real URL after initial navigation: {page.url}\n")

        if "/Disclaimer" in page.url:
            print("Step 2 — real disclaimer gate detected, attempting to accept it")
            try:
                accept_btn = page.get_by_role("button", name="Accept", exact=True)
                count = await accept_btn.count()
                print(f"  Real 'Accept' buttons found: {count}")
                await accept_btn.first.click(timeout=5_000)
                print("  Clicked real Accept button")
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1)
            except Exception as e:
                print(f"  ⚠ Could not accept disclaimer: {e}")
        else:
            print("Step 2 — no disclaimer gate this time, proceeding directly")

        print(f"\nReal URL after disclaimer handling: {page.url}\n")

        text = ""
        try:
            text = await page.locator("body").inner_text()
        except Exception as e:
            print(f"⚠ Could not read body text: {e}")

        print(f"Step 3 — real visible body text (first 2500 chars):\n{text[:2500]!r}\n")

        m = re.search(r"(?:Application )?Received Date\s*\n?\s*(\d{1,2}/\d{1,2}/\d{2,4})", text)
        print(f"Step 4 — real regex match: {m.group(0) if m else None}")
        if m:
            print(f"  Captured date: {m.group(1)}")
        else:
            print("  ⚠ No match — checking whether 'Received' appears anywhere at all")
            found = re.search(r"received", text, re.I)
            if found:
                idx = found.start()
                print(f"  Real context around 'received': {text[max(0,idx-40):idx+80]!r}")
            else:
                print("  The word 'received' does not appear anywhere on this real page")

        out_png = "/tmp/nwarks_verbose_diag.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
            print(f"\nSaved screenshot: {out_png}")
        except Exception:
            pass

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
