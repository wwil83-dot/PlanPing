#!/usr/bin/env python3
"""
PlanFind — Pembrokeshire/Slough criteria-URL compatibility test
(2026-09-03).

platform_family_verify.py confirmed Pembrokeshire and Slough are real,
live agileapplications.co.uk councils — but their visible UI (radio
buttons for search type, text fields, a Terms & Conditions gate) looks
structurally different from the confirmed criteria-URL + ng-table
pattern the existing 6 councils (Middlesbrough, Flintshire, Cannock,
Rugby, Dudley, Peterborough) use. Same domain/company, but possibly a
different UI generation — same "same platform branding doesn't mean
identical scraper works" lesson already learned with Fylde/Kirklees
this session.

This tests directly: does navigating straight to the same
?criteria={JSON}&page=1 URL shape (bypassing the visible form entirely)
still return the same real ng-table/animate-repeat structure the
existing scraper parses? If yes, these are trivial batch-adds after
all. If no, they need their own dedicated build like Harrow.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

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

TARGETS = ["pembrokeshire", "slough"]


async def test_one(browser, slug: str):
    print(f"\n{'=' * 70}")
    print(f"TESTING: {slug}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    # Real, confirmed shape from the existing working councils
    today = date.today()
    start = today - timedelta(days=30)
    criteria = (
        '{"status":"registered",'
        f'"registrationDateFrom":"{start.isoformat()}T00:00:00+01:00",'
        f'"registrationDateTo":"{today.isoformat()}T23:59:59+01:00"}}'
    )
    url = f"https://planning.agileapplications.co.uk/{slug}/search-applications/results?criteria={criteria}&page=1"
    print(f"URL: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        # Handle a Terms & Conditions gate if it appears — confirmed
        # present on Pembrokeshire/Slough's landing page, unconfirmed
        # whether it also blocks this direct criteria-URL route.
        try:
            accept_btn = page.get_by_text("Accept", exact=True)
            if await accept_btn.count() > 0:
                print("  Terms & Conditions gate appeared — clicking Accept")
                await accept_btn.first.click(timeout=5_000)
                await asyncio.sleep(2)
        except Exception:
            pass

        print(f"  Real final URL: {page.url}")
        title = await page.title()
        print(f"  Real page title: {title!r}")

        # Real, confirmed selector from the existing working councils
        rows = page.locator("tr.animate-repeat")
        row_count = await rows.count()
        print(f"  Real 'animate-repeat' rows found: {row_count}")

        if row_count > 0:
            first_row_text = await rows.first.inner_text()
            print(f"  First real row text: {first_row_text[:300]!r}")
        else:
            body_text = (await page.locator("body").inner_text())[:1000]
            print(f"  ⚠ No matching rows — real body text (first 1000 chars): {body_text!r}")

        html = await page.content()
        out_path = f"/tmp/agile_criteria_test_{slug}.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Saved: {out_path}")

    except Exception as e:
        print(f"  ⚠ Error: {type(e).__name__}: {e!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Pembrokeshire/Slough "
          f"criteria-URL compatibility test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for slug in TARGETS:
            await test_one(browser, slug)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
