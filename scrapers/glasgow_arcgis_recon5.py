#!/usr/bin/env python3
"""
PlanFind — Glasgow ArcGIS FeatureServer recon, round 5 (2026-09-03).

Two rounds of config-file reverse-engineering (generic public endpoint,
then Glasgow's own org host) both came back with empty dataSources,
empty item/webMap references, and zero literal FeatureServer URLs
anywhere — despite the dashboard clearly loading and displaying real
data (721 Major Applications, a real application list) when actually
viewed in a browser. That means the widgets ARE making real HTTP
requests to fetch that data at runtime — this captures those requests
directly via Playwright, rather than continuing to guess at Experience
Builder's internal config format.
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

DASHBOARD_URL = "https://experience.arcgis.com/experience/158560dc6db447cc9eeb4a40ca8c1e79/page/Home"

captured_requests = []


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow dashboard — real network capture\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        def on_request(request):
            url = request.url
            if re.search(r"(FeatureServer|MapServer)", url, re.IGNORECASE):
                captured_requests.append(url)

        page.on("request", on_request)

        print(f"\nLoading real dashboard: {DASHBOARD_URL}")
        try:
            await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=45_000)
            # Real dashboards often load their data asynchronously after
            # initial render — give it a real, generous window to finish
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(5)  # extra buffer for any slow async widget loads
        except Exception as e:
            print(f"⚠ Navigation error: {type(e).__name__}: {e!r}")

        print(f"\nReal FeatureServer/MapServer requests captured: {len(captured_requests)}")
        unique_bases = set()
        for url in captured_requests:
            base = url.split("?")[0]
            # Strip trailing /query, /0, etc. to get the real service root
            base_root = re.sub(r"/(query|\d+)(/query)?$", "", base)
            unique_bases.add(base_root)

        print(f"\nReal unique service roots found: {len(unique_bases)}")
        for base in unique_bases:
            print(f"  {base}")

        # Save full raw list too, in case multiple distinct layers under
        # the same root matter
        print(f"\nFull raw captured URLs ({len(captured_requests)}):")
        for url in captured_requests[:30]:
            print(f"  {url}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
