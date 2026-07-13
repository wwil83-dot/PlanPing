#!/usr/bin/env python3
"""
PlanFind — Arcus reconnaissance script, round 4: click-based portability test.

Round 3 proved the raw c__q URL parameter from Ashford's "Planning
Applications Weekly List" click does NOT transfer as a hardcoded shortcut
to other councils (Epping Forest, Manchester both showed 0 real content —
same "Sorry to interrupt" shell as an unclicked page). That parameter
likely encodes something session-bound or org-specific under the hood.

This is a DIFFERENT, more important test: does the CLICK-BASED approach
that worked for Ashford (navigate → wait → find visible link text → click
→ wait) ALSO work on other councils, even though the resulting URL isn't
reusable as a shortcut? If yes, this is genuinely good news — it means
a single scraper template (navigate + click + wait + read CSV) can work
across Arcus councils, it just needs to do the click every time rather
than skip straight to a URL, similar in spirit to how some of the harder
Idox councils already need form interaction rather than a clean URL param.

If the click ALSO fails on other councils (e.g. because they don't have
the exact same "Planning Applications Weekly List" link text, or Playwright
needs even more patience for slower Salesforce orgs), that tells us Arcus
scraping will need real per-council reconnaissance work rather than one
shared template — much closer to writing individual one-off scrapers.
"""
import asyncio
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

TEST_TARGETS = [
    {
        "name": "Epping Forest District Council",
        "url": "https://eppingforestdc.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register",
    },
    {
        "name": "Manchester City Council",
        "url": "https://arcusbe.manchester.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register",
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

# Same link-text variants tried in round 1, since we don't yet know if
# other councils phrase this identically to Ashford.
LINK_TEXT_VARIANTS = [
    "Planning Applications Weekly List",
    "Planning Application Weekly List",
    "Weekly List",
    "Weekly Lists",
]


async def test_council(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"Testing: {name}")
    print(f"{'=' * 70}")
    print(f"Starting URL: {url}\n")

    context = await browser.new_context(**CONTEXT_OPTIONS, accept_downloads=True)
    page = await context.new_page()

    # --- Stage 1: initial load ---
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(2)
    except Exception as e:
        print(f"⚠ Initial navigation error: {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        print("⚠ networkidle wait timed out (common for Lightning apps, not necessarily a problem)")
    await asyncio.sleep(5)

    homepage_html = await page.content()
    print(f"Homepage HTML length after wait: {len(homepage_html)} chars")

    # Print which of our known link-text variants actually appear on THIS
    # council's homepage, before even trying to click — useful even if the
    # click itself fails, since it tells us whether the wording differs.
    for variant in LINK_TEXT_VARIANTS:
        present = variant in homepage_html
        print(f"  Link text '{variant}' present in HTML: {present}")

    homepage_html_path = f"/tmp/arcus_click_{name.replace(' ', '_')}_00_homepage.html"
    with open(homepage_html_path, "w", encoding="utf-8") as f:
        f.write(homepage_html)
    await page.screenshot(path=f"/tmp/arcus_click_{name.replace(' ', '_')}_00_homepage.png", full_page=True)

    # --- Stage 2: try clicking through, same logic as round 1 ---
    clicked = False
    clicked_text = None
    for variant in LINK_TEXT_VARIANTS:
        try:
            loc = page.get_by_text(variant, exact=False)
            if await loc.count() > 0:
                await loc.first.click(timeout=5_000)
                clicked = True
                clicked_text = variant
                print(f"\n✓ Clicked link matching: '{variant}'")
                break
        except Exception:
            continue

    if not clicked:
        print("\n⚠ Could not find/click ANY weekly-list link variant on this council's homepage.")
        await context.close()
        return

    await asyncio.sleep(5)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    results_html = await page.content()
    print(f"\nPage title after click: '{await page.title()}'")
    print(f"HTML length after click: {len(results_html)} chars")

    indicators = {
        "Application Reference": results_html.count("Application Reference"),
        "Site Address": results_html.count("Site Address"),
        "Download as CSV": results_html.count("Download as CSV"),
        "Showing": results_html.count("Showing"),
    }
    for k, v in indicators.items():
        print(f"  Count of '{k}': {v}")

    results_html_path = f"/tmp/arcus_click_{name.replace(' ', '_')}_01_after_click.html"
    with open(results_html_path, "w", encoding="utf-8") as f:
        f.write(results_html)
    await page.screenshot(path=f"/tmp/arcus_click_{name.replace(' ', '_')}_01_after_click.png", full_page=True)
    print(f"Saved: {results_html_path}")

    # --- Stage 3: if it looks real, try the CSV download too ---
    if indicators["Application Reference"] > 0 or indicators["Download as CSV"] > 0:
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await page.get_by_text("Download as CSV", exact=False).first.click()
            download = await download_info.value
            csv_path = f"/tmp/arcus_click_{name.replace(' ', '_')}.csv"
            await download.save_as(csv_path)
            with open(csv_path, encoding="utf-8", errors="replace") as f:
                preview = f.read(600)
            print(f"\n✓✓✓ CSV download succeeded via CLICK-BASED approach on {name}!")
            print(f"--- CSV preview (first 600 chars) ---\n{preview}")
        except Exception as e:
            print(f"\n⚠ CSV download did not work on {name} even after successful click: {e}")
    else:
        print(f"\n⚠ Clicked '{clicked_text}' but no real application data appeared for {name}.")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon round 4 — click-based portability test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        for target in TEST_TARGETS:
            await test_council(browser, target["name"], target["url"])
        await browser.close()

    print(f"\n{'=' * 70}")
    print("Click-based portability test complete.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
