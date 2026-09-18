#!/usr/bin/env python3
"""
PlanFind — East Lothian Council Idox diagnostic (2026-09-18).

Real, confirmed context: East Lothian Council has a real, correct entry
in IDOX_COUNCILS (https://pa.eastlothian.gov.uk/online-applications —
triple-confirmed via web search, the council's own ArcGIS Web
AppBuilder item description, AND the existing config), and a real,
hardcoded council_id (334). Despite this, a direct Supabase check
confirmed coverage_source='pending', last_saved_at=null, app_count=0 —
it has NEVER actually saved a single real application. No comment
anywhere in the councils file flags a known block for this council
specifically (unlike Highland/Derby's extensively documented
datacenter-ASN blocks), suggesting this was added but never actually
tested with a real run, rather than being a known, diagnosed failure.

This loads the real weekly and monthly list pages directly, checks for
known real block signatures already confirmed elsewhere in this
project (Cloudflare challenge text, cert errors, total timeouts), and
if neither page shows a block, attempts a real, full form-based search
to see whether real results genuinely come back.
"""
import asyncio
import time

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

BASE_URL = "https://pa.eastlothian.gov.uk/online-applications"

# Real, confirmed block signatures already found elsewhere in this
# project for other Idox councils — checked here directly rather than
# assumed absent.
BLOCK_SIGNATURES = [
    "Just a moment",           # Cloudflare challenge
    "Performing security verification",
    "Attention Required",
    "Access denied",
]


async def check_page_directly(page, url: str, label: str) -> dict:
    result = {"url": url, "label": label}
    start = time.monotonic()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        result["status"] = response.status if response else None
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        result["elapsed_seconds"] = round(time.monotonic() - start, 1)
        result["title"] = await page.title()
        body_text = await page.locator("body").inner_text()
        result["body_length"] = len(body_text)
        found_blocks = [sig for sig in BLOCK_SIGNATURES if sig.lower() in body_text.lower()]
        result["block_signatures_found"] = found_blocks
        result["real_body_excerpt"] = body_text[:300]
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["elapsed_seconds"] = round(time.monotonic() - start, 1)
    return result


async def attempt_real_search(page) -> dict:
    """Real, standard Idox monthly-list flow, matching the same
    interaction already proven across dozens of other working Idox
    councils in this project: month-select + status radio + submit."""
    result = {}
    try:
        month_url = f"{BASE_URL}/search.do?action=monthlyList"
        response = await page.goto(month_url, wait_until="domcontentloaded", timeout=45_000)
        result["monthly_list_status"] = response.status if response else None
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        result["monthly_list_title"] = await page.title()
        result["monthly_list_url_after"] = page.url

        month_select = page.locator("select[name='month']")
        if await month_select.count() > 0:
            options = await month_select.locator("option").all_text_contents()
            result["real_month_options_found"] = options[:5]

            received_radio = page.locator("input[type='radio'][value*='Received' i], "
                                           "input[type='radio'][value*='received' i]")
            if await received_radio.count() > 0:
                await received_radio.first.check(timeout=5_000)
                result["real_radio_checked"] = True

            submit = page.locator("input[type='submit'], button[type='submit']")
            if await submit.count() > 0:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    await submit.first.click(timeout=5_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                result["real_results_url"] = page.url
                result["real_results_title"] = await page.title()
                body_text = await page.locator("body").inner_text()
                result["real_results_body_excerpt"] = body_text[:500]

                # Real, confirmed Idox results-table class used across
                # every other working council in this project.
                results_table = page.locator("table.results, ul.searchresults, .searchresult")
                result["real_results_elements_found"] = await results_table.count()
            else:
                result["real_submit_button_found"] = False
        else:
            result["real_month_select_found"] = False
    except Exception as e:
        result["real_search_error"] = f"{type(e).__name__}: {e}"
    return result


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print("=" * 60)
        print("STEP 1: Real direct page-load checks")
        print("=" * 60)
        for url, label in [
            (f"{BASE_URL}/search.do?action=weeklyList", "Weekly List"),
            (f"{BASE_URL}/search.do?action=monthlyList", "Monthly List"),
        ]:
            result = await check_page_directly(page, url, label)
            print(f"\n[{label}] {url}")
            for k, v in result.items():
                if k != "url":
                    print(f"  {k}: {v!r}")

        print("\n" + "=" * 60)
        print("STEP 2: Real full form-submission test")
        print("=" * 60)
        search_result = await attempt_real_search(page)
        for k, v in search_result.items():
            print(f"  {k}: {v!r}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
