#!/usr/bin/env python3
"""
PlanFind — Eden/South Lakeland real pagination URL confirmation (2026-08-22).

Real, confirmed: the 'Next' link uses data-ajax-target=
"/Search/ResultsPage/2?module=PLA" — a jQuery Unobtrusive AJAX pattern,
not a plain href. Testing whether this real URL works via plain direct
navigation, or whether it only responds correctly to a genuine AJAX
request (X-Requested-With header etc).
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Eden/South Lakeland real pagination URL confirmation\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

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

        html1 = await page.content()
        refs1 = get_refs(html1)
        print(f"Page 1 (via real search submission): {len(refs1)} refs: {refs1}\n")

        # Test 1 — plain direct navigation (same session/cookies)
        test_url = "https://planningregister.westmorlandandfurness.gov.uk/Search/ResultsPage/2?module=PLA"
        print(f"TEST 1 — plain direct navigation to: {test_url}")
        try:
            resp = await page.goto(test_url, wait_until="domcontentloaded", timeout=20_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            print(f"  HTTP status: {resp.status if resp else None}")
            html2 = await page.content()
            refs2 = get_refs(html2)
            print(f"  Refs found: {len(refs2)}: {refs2}")
            print(f"  Real content length: {len(html2)} chars")
            overlap = set(refs1) & set(refs2)
            print(f"  Overlap with page 1: {overlap}")
            print(f"  GENUINELY DIFFERENT: {len(overlap) == 0 and len(refs2) > 0}\n")
        except Exception as e:
            print(f"  ⚠ Error: {e}\n")

        # Test 2 — real AJAX-style request with the header ASP.NET
        # unobtrusive-ajax actually sends, in case plain navigation
        # doesn't work
        print(f"TEST 2 — real AJAX-style request (X-Requested-With header)")
        try:
            api_response = await page.request.get(
                test_url,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            print(f"  HTTP status: {api_response.status}")
            body = await api_response.text()
            refs3 = get_refs(body)
            print(f"  Refs found: {len(refs3)}: {refs3}")
            print(f"  Real content length: {len(body)} chars")
            overlap2 = set(refs1) & set(refs3)
            print(f"  Overlap with page 1: {overlap2}")
            print(f"  GENUINELY DIFFERENT: {len(overlap2) == 0 and len(refs3) > 0}")
        except Exception as e:
            print(f"  ⚠ Error: {e}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("CONFIRMATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
