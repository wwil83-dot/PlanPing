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
            # REAL FIX — confirmed via direct user testing: the real
            # field is "Date Application Received"
            # (dateApprecFrom/dateApprecTo), NOT "Date Application
            # Valid" (dateAppValidFrom/dateAppValidTo) — a genuine
            # mistake in the original field selection, not any real
            # framework/automation limitation. The empty network
            # request from the earlier round was simply because the
            # wrong field was being searched.
            await page.locator("#dateApprecFrom").first.fill(start.strftime("%Y-%m-%d"), timeout=5_000)
            await page.locator("#dateApprecTo").first.fill(today.strftime("%Y-%m-%d"), timeout=5_000)
            print(f"Filled real dates via native .fill() on the CORRECT field: "
                  f"{start.isoformat()} to {today.isoformat()}\n")
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

        print(f"\nReal URL after search: {page.url}")
        title = await page.title()
        print(f"Real page title: {title!r}\n")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:1500]
        except Exception:
            pass
        print(f"Real visible body text (first 1500 chars): {body_text!r}\n")

        html = await page.content()
        out_html = "/tmp/stratford_correct_results.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/stratford_correct_results.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"Saved: {out_html}, {out_png}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("CAPTURE COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
