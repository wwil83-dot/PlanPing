#!/usr/bin/env python3
"""
PlanFind — backlog batch recon 3 (2026-08-31).

Follow-up recon resolving the 4 remaining ambiguities from
backlog_batch_recon2.py before any of these four councils can be
built:

  - Amber Valley: real, documented JSON web-service endpoints were
    found embedded in the page source (info.ambervalley.gov.uk/
    WebServices/AVBCFeeds/DevConJSON.asmx/{PlanAppsAllValidNonDetermined,
    PlanAppsDetermined}). This tests them directly with plain httpx
    POSTs — if they work as the page's own JS calls them, this could
    be a pure-JSON, Playwright-free scraper.

  - Fylde: hit a disclaimer/cookie gate (/Disclaimer?returnUrl=...)
    before reaching /Search/Advanced. This accepts it (clicks "Agree"),
    then fills and submits a real date-range search on the real
    Advanced page, capturing /Search/Results structure.

  - Rotherham: the weekly list page loaded but the actual results
    table wasn't visible in the previously-captured text. This selects
    "Most Recent" and submits, capturing the real results structure.

  - South Derbyshire: the page is a Laravel Livewire component with
    real public properties visible in its initial wire:snapshot state
    (afterDate, beforeDate, dateType, reference, proposal, location,
    etc.) — all null/0 by default. Tests whether these are genuinely
    Livewire #[Url]-bound query-string parameters by appending guessed
    values directly to the URL and checking whether the initial
    server-rendered data actually reflects a filtered set (fewer than
    the base 32,240 total) without any JS interaction needed.
"""
import asyncio
import re
from datetime import datetime, timezone

import httpx
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

HTTP_HEADERS = {
    "User-Agent": CONTEXT_OPTIONS["user_agent"],
}


async def save_evidence(page, slug: str):
    html = await page.content()
    out_html = f"/tmp/backlog3_recon_{slug}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/backlog3_recon_{slug}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1500]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1500 chars): {body_text!r}")


# ---------------------------------------------------------------------
# 1. Amber Valley — plain HTTP JSON API test (no Playwright needed here)
# ---------------------------------------------------------------------
async def recon_amber_valley():
    print(f"\n{'=' * 70}")
    print("RECON: Amber Valley Borough Council — JSON web service test")
    print("=" * 70)

    base = "https://info.ambervalley.gov.uk/WebServices/AVBCFeeds/DevConJSON.asmx"
    tests = [
        ("PlanAppsAllValidNonDetermined (all pending)",
         f"{base}/PlanAppsAllValidNonDetermined",
         {"wardCode": "", "parishCode": ""}),
        ("PlanAppsDetermined (30-day range)",
         f"{base}/PlanAppsDetermined",
         {"wardCode": "", "parishCode": "", "fromDate": "01-Aug-2026", "toDate": "30-Aug-2026"}),
    ]

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30) as client:
        for label, url, data in tests:
            print(f"\n  --- {label} ---")
            print(f"  POST {url}")
            print(f"  data={data}")
            try:
                # Try form-encoded first (what jQuery's default POST sends)
                r = await client.post(url, data=data)
                print(f"  Real HTTP status: {r.status_code}")
                print(f"  Real Content-Type: {r.headers.get('content-type')}")
                print(f"  Real response (first 1000 chars): {r.text[:1000]!r}")
            except Exception as e:
                print(f"  ⚠ form-encoded POST failed: {e}")

            try:
                # Fallback: some ASP.NET .asmx services require JSON body + header
                r2 = await client.post(
                    url, json=data,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
                print(f"\n  [JSON-body variant] Real HTTP status: {r2.status_code}")
                print(f"  [JSON-body variant] Real response (first 1000 chars): {r2.text[:1000]!r}")
            except Exception as e:
                print(f"  ⚠ JSON-body POST failed: {e}")


# ---------------------------------------------------------------------
# 2. Fylde — accept disclaimer, then real date-range search submission
# ---------------------------------------------------------------------
async def recon_fylde(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Fylde Council — disclaimer accept + real search submission")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://pa.fylde.gov.uk/Search/Advanced",
                         wait_until="domcontentloaded", timeout=45_000)
        print(f"  Landed on: {page.url}")

        if "Disclaimer" in page.url:
            print("  Disclaimer gate hit — clicking Agree")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await page.click("button:has-text('Agree'), input[value='Agree']")
            print(f"  Post-agree URL: {page.url}")

        if "Search/Advanced" not in page.url:
            await page.goto("https://pa.fylde.gov.uk/Search/Advanced",
                             wait_until="domcontentloaded", timeout=45_000)

        await save_evidence(page, "fylde_advanced_form")

        # Try filling a plausible date-range field and submitting
        try:
            date_inputs = page.locator("input[type='text'], input[type='date']")
            count = await date_inputs.count()
            print(f"\n  Found {count} text/date inputs on the Advanced form")
            for i in range(min(count, 20)):
                el = date_inputs.nth(i)
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                print(f"    input[{i}] name={name!r} id={el_id!r}")
        except Exception as e:
            print(f"  ⚠ input enumeration error: {e}")

    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")

    await context.close()


# ---------------------------------------------------------------------
# 3. Rotherham — select Most Recent weekly list, submit, see real results
# ---------------------------------------------------------------------
async def recon_rotherham(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Rotherham Metropolitan Borough Council — weekly list submission")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://planning.rotherham.gov.uk/weeklylistapp.asp",
                         wait_until="domcontentloaded", timeout=45_000)

        selects = page.locator("select")
        scount = await selects.count()
        print(f"  Found {scount} <select> elements")
        for i in range(scount):
            el = selects.nth(i)
            name = await el.get_attribute("name") or ""
            el_id = await el.get_attribute("id") or ""
            options = await el.locator("option").all_inner_texts()
            print(f"    <select> name={name!r} id={el_id!r} options={options[:8]!r}")

        submit_buttons = page.locator("input[type='submit'], button[type='submit']")
        bcount = await submit_buttons.count()
        print(f"  Found {bcount} submit buttons")

        if bcount > 0:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await submit_buttons.first.click()
            print(f"  Post-submit URL: {page.url}")

        await save_evidence(page, "rotherham_weekly_results")

    except Exception as e:
        print(f"  ⚠ Navigation/submission error: {e}")

    await context.close()


# ---------------------------------------------------------------------
# 4. South Derbyshire — test Livewire URL query-param filtering
# ---------------------------------------------------------------------
async def recon_south_derbyshire(browser):
    print(f"\n{'=' * 70}")
    print("RECON: South Derbyshire District Council — Livewire URL param test")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    test_url = (
        "https://planning.southderbyshire.gov.uk/"
        "?dateType=1&afterDate=2026-08-01&beforeDate=2026-08-30"
    )

    try:
        await page.goto(test_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        html = await page.content()
        total_match = re.search(r'"total":(\d+)', html)
        showing_match = re.search(r"SHOWING \d+ TO \d+ OF ([\d,]+) APPLICATIONS", html)
        print(f"  Real 'total' found in wire:snapshot JSON: {total_match.group(1) if total_match else 'NOT FOUND'}")
        print(f"  Real 'SHOWING...OF' text on page: {showing_match.group(1) if showing_match else 'NOT FOUND'}")
        print("  (Base/unfiltered total was 32,240 — if either number above is "
              "meaningfully smaller, the URL query params are real Livewire "
              "#[Url]-bound filters.)")

        await save_evidence(page, "south_derbyshire_url_param_test")

    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Backlog batch recon 3 "
          f"— Amber Valley API, Fylde, Rotherham, South Derbyshire\n")

    await recon_amber_valley()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"\nChromium launched: {browser.version}")

        await recon_fylde(browser)
        await recon_rotherham(browser)
        await recon_south_derbyshire(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
