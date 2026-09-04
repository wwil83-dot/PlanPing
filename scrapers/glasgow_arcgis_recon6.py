#!/usr/bin/env python3
"""
PlanFind — Glasgow ArcGIS FeatureServer recon, round 6 (2026-09-03).

Round 5 captured ZERO FeatureServer/MapServer requests at all — but
that script had a real gap: it never saved a screenshot, HTML dump, or
any console/error diagnostics, unlike every other recon in this
project. We have no visibility into whether the page actually rendered
correctly (and simply didn't trigger those specific requests) or hit
some other real problem (JS error, login wall, headless-specific
rendering failure — Experience Builder apps are heavy React SPAs that
can behave differently in headless Chromium).

This captures everything: a screenshot, the full rendered HTML, all
console messages and page errors, and ALL network requests (not just
ones matching FeatureServer/MapServer) so we can see what's actually
happening rather than guessing again.
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

DASHBOARD_URL = "https://experience.arcgis.com/experience/158560dc6db447cc9eeb4a40ca8c1e79/page/Home"

all_requests = []
console_messages = []
page_errors = []


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow dashboard — full diagnostic capture\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        page.on("request", lambda r: all_requests.append(r.url))
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        print(f"\nLoading: {DASHBOARD_URL}")
        try:
            response = await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=45_000)
            print(f"Real initial HTTP status: {response.status if response else 'None'}")
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except PlaywrightTimeout:
                print("(networkidle timeout — page may still be loading async content)")
            await asyncio.sleep(8)  # generous extra buffer
        except Exception as e:
            print(f"⚠ Navigation error: {type(e).__name__}: {e!r}")

        print(f"\nReal final URL: {page.url}")
        title = await page.title()
        print(f"Real page title: {title!r}")

        # Save everything for real inspection
        html = await page.content()
        with open("/tmp/glasgow_dashboard_diag.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved: /tmp/glasgow_dashboard_diag.html")

        try:
            await page.screenshot(path="/tmp/glasgow_dashboard_diag.png", full_page=True)
            print("Saved: /tmp/glasgow_dashboard_diag.png")
        except Exception as e:
            print(f"⚠ Screenshot failed: {e}")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:2000]
        except Exception:
            pass
        print(f"\nReal visible body text (first 2000 chars): {body_text!r}")

        print(f"\nTotal real network requests captured: {len(all_requests)}")
        # Show a representative sample — first 20 and any that look
        # data-related (json, arcgis, esri) even if not Feature/MapServer
        interesting = [u for u in all_requests if any(
            k in u.lower() for k in ("arcgis", "esri", ".json", "/rest/")
        )]
        print(f"Real requests containing arcgis/esri/.json/rest ({len(interesting)}):")
        for u in interesting[:30]:
            print(f"  {u}")

        print(f"\nReal console messages captured: {len(console_messages)}")
        for msg in console_messages[:20]:
            print(f"  {msg}")

        print(f"\nReal page errors captured: {len(page_errors)}")
        for err in page_errors[:10]:
            print(f"  {err}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
