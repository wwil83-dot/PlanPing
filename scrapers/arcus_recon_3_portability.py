#!/usr/bin/env python3
"""
PlanFind — Arcus reconnaissance script, round 3: portability test.

Round 2 revealed that clicking "Planning Applications Weekly List" builds a
URL with a c__q parameter that decodes to a generic-looking JSON payload —
no Ashford-specific IDs, just human-readable developer names:

  {"register": "Arcus_BE_Public_Register", "requests": [{"registerName":
  "Arcus_BE_Public_Register", "searchType": "quick-link", "label":
  "Planning Applications Weekly List", "searchName":
  "Planning_Applications_Weekly_List"}]}

This script tests the single most important open question: does that EXACT
SAME encoded c__q value work if pasted directly into a DIFFERENT council's
register-view URL — skipping the "find and click the visible link text"
step entirely? Testing against two councils with different domain shapes:
  - Epping Forest: same my.site.com pattern as Ashford
  - Manchester: custom domain (arcusbe.manchester.gov.uk), not my.site.com

If this works on both, it means one shared, hardcoded URL construction
works across (at least most) Arcus councils — the equivalent of Idox's
reusable /online-applications/search.do pattern. If it only works on one,
or neither, that tells us portability is more limited than hoped and the
production scraper will need the click-based approach instead (slower,
more fragile, but still workable — same category of complexity as Idox's
harder councils).
"""
import asyncio
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# The exact c__q value captured from Ashford's "Planning Applications
# Weekly List" quick-link click in round 2 — reused verbatim here.
ASHFORD_WEEKLY_LIST_C_Q = (
    "eyJyZWdpc3RlciI6IkFyY3VzX0JFX1B1YmxpY19SZWdpc3RlciIsInJlcXVlc3RzIjpbeyJyZWdpc3Rlck5hbWUiO"
    "iJBcmN1c19CRV9QdWJsaWNfUmVnaXN0ZXIiLCJzZWFyY2hUeXBlIjoicXVpY2stbGluayIsImxhYmVsIjoiUGxhbm5"
    "pbmcgQXBwbGljYXRpb25zIFdlZWtseSBMaXN0Iiwic2VhcmNoTmFtZSI6IlBsYW5uaW5nX0FwcGxpY2F0aW9uc19XZ"
    "WVrbHlfTGlzdCJ9XX0%3D"
)

TEST_TARGETS = [
    {
        "name": "Epping Forest District Council",
        "base_url": "https://eppingforestdc.my.site.com/pr/s/register-view",
    },
    {
        "name": "Manchester City Council",
        "base_url": "https://arcusbe.manchester.gov.uk/pr/s/register-view",
    },
]

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
}


async def test_council(browser, name: str, base_url: str):
    print(f"\n{'=' * 70}")
    print(f"Testing: {name}")
    print(f"{'=' * 70}")

    context = await browser.new_context(**CONTEXT_OPTIONS, accept_downloads=True)
    page = await context.new_page()

    direct_url = f"{base_url}?c__r=Arcus_BE_Public_Register&c__q={ASHFORD_WEEKLY_LIST_C_Q}"
    print(f"Direct URL (built with Ashford's c__q, unmodified): {direct_url}\n")

    try:
        await page.goto(direct_url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(2)
    except Exception as e:
        print(f"⚠ Navigation error (this may just mean the domain/path differs): {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(5)

    title = await page.title()
    html = await page.content()
    print(f"Page title: '{title}'")
    print(f"HTML length: {len(html)} chars")

    # Diagnostic: does this look like it actually rendered real results,
    # or did we land on an error/login page, or the same loading shell?
    indicators = {
        "Application Reference": html.count("Application Reference"),
        "Site Address": html.count("Site Address"),
        "Download as CSV": html.count("Download as CSV"),
        "Sorry to interrupt": html.count("Sorry to interrupt"),
        "Showing": html.count("Showing"),
    }
    for k, v in indicators.items():
        print(f"  Count of '{k}': {v}")

    html_path = f"/tmp/arcus_portability_{name.replace(' ', '_')}.html"
    png_path = f"/tmp/arcus_portability_{name.replace(' ', '_')}.png"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        await page.screenshot(path=png_path, full_page=True)
    except Exception as e:
        print(f"  ⚠ Screenshot failed: {e}")
    print(f"Saved: {html_path}, {png_path}")

    # If it looks like real results rendered, try the CSV download too —
    # the real proof this is fully portable, not just visually similar.
    if indicators["Application Reference"] > 0 or indicators["Download as CSV"] > 0:
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await page.get_by_text("Download as CSV", exact=False).first.click()
            download = await download_info.value
            csv_path = f"/tmp/arcus_portability_{name.replace(' ', '_')}.csv"
            await download.save_as(csv_path)
            with open(csv_path, encoding="utf-8", errors="replace") as f:
                preview = f.read(600)
            print(f"\n✓ CSV download succeeded on {name}!")
            print(f"--- CSV preview (first 600 chars) ---\n{preview}")
        except Exception as e:
            print(f"\n⚠ CSV download did not work on {name}: {e}")
    else:
        print(f"\n⚠ Page does not appear to show real application data for {name} — "
              "the direct URL shortcut likely didn't transfer cleanly here.")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon round 3 — portability test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        for target in TEST_TARGETS:
            await test_council(browser, target["name"], target["base_url"])
        await browser.close()

    print(f"\n{'=' * 70}")
    print("Portability test complete.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
