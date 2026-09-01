#!/usr/bin/env python3
"""
PlanFind — Ribble Valley recon (2026-09-01).

New intel from the user's own browsing (not the original manual recon
list, which only found PDF weekly lists): a real HTML advanced search
exists at webportal.ribblevalley.gov.uk/planningApplication/search/
advanced, with a genuine results page reached via a plain GET query
string (no visible session token in the URL) — promising for a
simpler httpx-based scraper if it holds up, similar to West
Dunbartonshire/Ipswich, rather than needing the pdf skill.

Real, confirmed from screenshots (not yet verified in raw HTML):
  - Fields: Location Search, Applicant Name, Development Description
    (all free text); Decision Type + Year (dropdowns, for a
    decision-type-in-a-year search); Decision Date Between (day/month/
    year dropdowns, real range search — no visible field for a
    RECEIVED/submitted date range, only decision date).
  - Results table: App N° (linked) + Applicant (name/address combined)
    — no proposal, status, or date visible in the list view itself.

This recon: submits the real Decision Date Between search, captures
the real results page HTML (to get exact query param names from the
resulting URL), and follows one detail link to see the real
per-application page structure (proposal, status, dates, address).
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

SEARCH_URL = "https://webportal.ribblevalley.gov.uk/planningApplication/search/advanced"


async def save_evidence(page, slug: str):
    html = await page.content()
    out_html = f"/tmp/ribblevalley_recon_{slug}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/ribblevalley_recon_{slug}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")


async def dump_form_fields(page):
    print("\n  Real form fields found on this page:")
    for tag in ("input", "select"):
        els = page.locator(tag)
        count = await els.count()
        for i in range(min(count, 30)):
            el = els.nth(i)
            try:
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                itype = await el.get_attribute("type") or ""
                print(f"    <{tag}> type={itype!r} name={name!r} id={el_id!r}")
            except Exception:
                pass


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Ribble Valley recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print(f"\n{'=' * 70}")
        print("STEP 1: Load the real advanced search form")
        print("=" * 70)
        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
            print(f"  Real final URL: {page.url}")
            print(f"  Real page title: {await page.title()!r}")
            await dump_form_fields(page)
            await save_evidence(page, "advanced_form")
        except Exception as e:
            print(f"  ⚠ Navigation error: {type(e).__name__}: {e!r}")
            await context.close()
            await browser.close()
            return

        print(f"\n{'=' * 70}")
        print("STEP 2: Submit a real Decision Date Between search")
        print("=" * 70)
        try:
            # Real, confirmed from screenshot: 3 separate dropdowns per
            # date (day/month/year) — real select element names/ids
            # unknown until STEP 1's dump above confirms them. Trying
            # common patterns first; the dump output is the real source
            # of truth if these don't match.
            selects = page.locator("select")
            scount = await selects.count()
            print(f"  Found {scount} real <select> elements — attempting "
                  f"to fill the Decision Date Between range generically "
                  f"by position (see STEP 1 dump above for real name/id "
                  f"to hand-fix this if positions are wrong)")

            # This is a best-effort attempt given we don't have real
            # name/id confirmed yet — see STEP 1's dump for ground truth.
            if scount >= 8:
                # Guessed order: [decisionType, year, from-day, from-month,
                # from-year, to-day, to-month, to-year]
                await selects.nth(2).select_option(label="1")
                await selects.nth(3).select_option(label="Aug")
                await selects.nth(4).select_option(label="2026")
                await selects.nth(5).select_option(label="31")
                await selects.nth(6).select_option(label="Aug")
                await selects.nth(7).select_option(label="2026")

            submit = page.locator("button:has-text('Search'), input[type='submit']").last
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await submit.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass

            print(f"  Real results URL (shows exact query param names): {page.url}")
            body_text = (await page.locator("body").inner_text())[:1500]
            print(f"  Real visible body text (first 1500 chars): {body_text!r}")
            await save_evidence(page, "results")

        except Exception as e:
            print(f"  ⚠ Search fill/submit error: {type(e).__name__}: {e!r}")

        print(f"\n{'=' * 70}")
        print("STEP 3: Follow one real detail link")
        print("=" * 70)
        try:
            first_link = page.locator("table a").first
            if await first_link.count() > 0:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    await first_link.click()
                print(f"  Real detail URL: {page.url}")
                body_text = (await page.locator("body").inner_text())[:2000]
                print(f"  Real detail page body text (first 2000 chars): {body_text!r}")
                await save_evidence(page, "detail_page")
            else:
                print("  ⚠ No result links found to follow")
        except Exception as e:
            print(f"  ⚠ Detail page navigation error: {type(e).__name__}: {e!r}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
