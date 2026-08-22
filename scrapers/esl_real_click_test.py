#!/usr/bin/env python3
"""
PlanFind — Eden/South Lakeland real Next click, done properly (2026-08-22).

Real, embarrassing gap in the previous test: it saw the empty href on
the real "Next" link and returned early rather than actually clicking
it. With a data-ajax-target attribute, the real navigation happens via
jQuery unobtrusive-ajax's own JS handler intercepting the click — that
handler knows how to correctly replicate whatever real session/anti-
forgery state is needed, which manually reconstructing the target URL
myself could never do. Just clicking it directly this time, in the
same live page/session, and checking what actually happens.
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


def get_refs(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    refs = []
    for r in table.find_all("tr")[1:]:
        cells = r.find_all("td")
        if cells:
            a = cells[0].find("a")
            if a:
                refs.append(a.get_text(strip=True))
    return refs


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Eden/South Lakeland real Next click\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        # Log every real network request/response during the click, so
        # we can see exactly what the real JS handler actually does
        network_log = []
        page.on("response", lambda r: network_log.append((r.request.method, r.url, r.status)))

        await page.goto("https://planningregister.westmorlandandfurness.gov.uk/Search/Advanced",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        today = date.today()
        start = today - timedelta(days=30)
        await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
        await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)
        await page.locator("button:has-text('Search')").first.click(timeout=5_000)

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        refs1 = get_refs(await page.content())
        print(f"Page 1: {len(refs1)} refs: {refs1}\n")

        network_log.clear()
        real_url_before = page.url

        print("Clicking the real 'Next' link directly...")
        try:
            await page.get_by_text("Next", exact=True).first.click(timeout=10_000)
        except Exception as e:
            print(f"⚠ Click failed: {e}")
            await context.close()
            await browser.close()
            return

        # Real, active wait for the AJAX call to actually complete and
        # the DOM to update, rather than a blind guess
        await asyncio.sleep(3)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        print(f"\nReal URL after click (may be unchanged if AJAX replaced content in-place): {page.url}")
        print(f"Real URL changed: {page.url != real_url_before}\n")

        print(f"Real network activity during/after the click ({len(network_log)} responses):")
        for method, url, status in network_log:
            if "westmorlandandfurness" in url:
                print(f"  {method} {status} {url}")

        html2 = await page.content()
        refs2 = get_refs(html2)
        print(f"\nAfter click: {len(refs2)} refs: {refs2}")

        overlap = set(refs1) & set(refs2)
        print(f"Overlap with page 1: {overlap}")
        print(f"GENUINELY DIFFERENT PAGE: {len(overlap) == 0 and len(refs2) > 0}")

        out_png = "/tmp/esl_next_click_result.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
            print(f"\nSaved screenshot: {out_png}")
        except Exception:
            pass

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
