#!/usr/bin/env python3
"""
PlanFind — Stratford real network request capture (2026-08-27).

Real, confirmed twice now: "No Results Found" even with dates
genuinely set via JS and a real Search button confirmed clicked.
Rather than guess further, capturing the actual real network request
sent on submission — this will show definitively whether the
JS-set date values ever made it into the real search query at all, or
whether this framework reads from its own internal state rather than
the raw DOM value (a well-known category of web-automation gotcha).
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


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Stratford real network request capture\n")

    requests_log = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        def on_request(request):
            if "SearchResult" in request.url or request.method == "POST":
                requests_log.append({
                    "method": request.method,
                    "url": request.url,
                    "post_data": request.post_data,
                })

        page.on("request", on_request)

        try:
            await page.goto("https://apps.stratford.gov.uk/eplanningv2/Home/AdvancedSearch",
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
            accept_btn = page.get_by_text("Accept", exact=True)
            if await accept_btn.count() > 0:
                await accept_btn.first.click(timeout=5_000)
                await asyncio.sleep(1)
        except Exception:
            pass

        today = date.today()
        start = today - timedelta(days=30)

        try:
            # REAL FIX — confirmed via direct network capture: the
            # previous JS-injection approach (set .value + dispatch
            # input/change) correctly updated the raw DOM, but the
            # real click handler completely ignored it and submitted a
            # bare, parameter-less GET request regardless — this
            # framework reads from its own internal state model, not
            # the raw DOM. Using Playwright's native .fill() instead,
            # which triggers a real, complete sequence of keyboard/
            # input events that framework-level listeners are actually
            # built to catch.
            await page.locator("#dateAppValidFrom").first.fill(start.strftime("%Y-%m-%d"), timeout=5_000)
            await page.locator("#dateAppValidTo").first.fill(today.strftime("%Y-%m-%d"), timeout=5_000)
            print(f"Filled real dates via native .fill(): {start.isoformat()} to {today.isoformat()}\n")
        except Exception as e:
            print(f"⚠ Could not fill date fields: {e}")
            await browser.close()
            return

        requests_log.clear()

        try:
            search_btn = page.locator("button:has-text('Search')")
            await search_btn.first.click(timeout=8_000)
        except Exception as e:
            print(f"⚠ Could not click Search: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1.5)

        print(f"Real captured requests during/after the click ({len(requests_log)} total):")
        for r in requests_log:
            print(f"\n  {r['method']} {r['url']}")
            if r['post_data']:
                print(f"    Real POST data: {r['post_data'][:1000]}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("CAPTURE COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
