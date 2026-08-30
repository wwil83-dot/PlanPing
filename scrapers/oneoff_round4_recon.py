#!/usr/bin/env python3
"""
PlanFind — Ipswich / Central Bedfordshire round 4 recon (2026-08-30).

Round 3 confirmed both councils have genuine district-wide date-range
advanced search forms (see oneoff_round3_recon.py's docstring for the
full background). This round actually SUBMITS a real date-range search
on each and captures the resulting results page — the last unknown
before either scraper can be written for real: results table structure
(columns, per-application links, pagination signature).

  - Ipswich: fills 'Date Valid Application Received' from/to fields on
    appnsearch.asp and clicks the image-button Search — form posts
    plainly to appnresults.asp with no session token.

  - Central Bedfordshire (AcolNet): fills 'Registration Date From/To'
    on the advanced search page and clicks Search. The form action
    carries a session-bound RIPSESSION token, but since Playwright
    drives the live page/DOM, fill+click handles that automatically —
    no manual token parsing needed.

A ~2 month window is used for both, wide enough to very likely return
real results without being so wide it risks a "too many records"
timeout.
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

DATE_FROM = "01/07/2026"
DATE_TO = "30/08/2026"


async def save_evidence(page, slug: str):
    html = await page.content()
    out_html = f"/tmp/oneoff_r4_{slug}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/oneoff_r4_{slug}.png"
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
    print(f"\n  Real visible body text (first 2000 chars): {body_text!r}")

    # Dump links and table structure — the actual thing this round exists for
    try:
        links = page.locator("a")
        count = await links.count()
        print(f"\n  Real links found ({min(count, 40)} of {count} shown):")
        for i in range(min(count, 40)):
            el = links.nth(i)
            try:
                href = await el.get_attribute("href") or ""
                text = (await el.inner_text()).strip()
                if href and text:
                    print(f"    {text!r} -> {href!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ link dump error: {e}")

    try:
        tables = page.locator("table")
        tcount = await tables.count()
        print(f"\n  {tcount} <table> element(s) found on results page")
    except Exception:
        pass


async def recon_ipswich(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Ipswich Borough Council — real date-range search submission")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(
            "https://ppc.ipswich.gov.uk/appnsearch.asp",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        await page.fill("#Text1", DATE_FROM)   # txtValStartDate
        await page.fill("#Text2", DATE_TO)     # txtValEndDate
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
            await page.click("#imgSubmit")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation/submission error: {e}")
        await context.close()
        return

    print(f"  Real final URL: {page.url}")
    print(f"  Real page title: {await page.title()!r}")
    await save_evidence(page, "ipswich_results")
    await context.close()


async def recon_central_beds(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Central Bedfordshire Council — real date-range search submission")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(
            "https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/"
            "acolnetcgi.gov?ACTION=UNWRAP&RIPNAME=Root.pgesearch",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        await page.fill("#regdate1", DATE_FROM)
        await page.fill("#regdate2", DATE_TO)
        search_buttons = page.locator("button:has-text('Search'), input[type='submit'][value='Search'], input[type='button'][value='Search']")
        btn_count = await search_buttons.count()
        if btn_count == 0:
            # Fallback: any element whose visible text is exactly 'Search'
            search_buttons = page.get_by_text("Search", exact=True)
            btn_count = await search_buttons.count()
        print(f"  Found {btn_count} candidate Search button(s), clicking the last one")
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
            await search_buttons.nth(max(btn_count - 1, 0)).click()
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation/submission error: {e}")
        await context.close()
        return

    print(f"  Real final URL: {page.url}")
    print(f"  Real page title: {await page.title()!r}")
    await save_evidence(page, "central_beds_results")
    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Round 4 recon "
          f"— real date-range search submission ({DATE_FROM} to {DATE_TO})\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await recon_ipswich(browser)
        await recon_central_beds(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
