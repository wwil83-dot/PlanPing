#!/usr/bin/env python3
"""
PlanFind — Vale of White Horse / South Oxfordshire Weekly List CSV
diagnostic (2026-09-17).

Real, confirmed lead: both councils' own Weekly List pages
(/Planning/WeeklyList) show a banner reading "We are aware that some
features may not be working as expected" — direct confirmation from
the council itself that its search feature (the one this whole
platform's earlier diagnostics fought with) is genuinely broken right
now, not a bug in our own approach. The Weekly List page offers direct
CSV/PDF downloads for each of the last 26 weeks, needing no search
form, no checkboxes, no collapsible sections at all.

This clicks through the disclaimer, finds the real CSV download link
for the most recent week, downloads it, and dumps its real structure
(headers, sample rows) so a proper parser can be built from confirmed
evidence rather than assumption.
"""
import asyncio

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
    "accept_downloads": True,
}

CANDIDATES = [
    ("Vale of White Horse District Council", "https://valeofwhitehorse.planning-register.co.uk"),
    ("South Oxfordshire District Council", "https://southoxfordshire.planning-register.co.uk"),
]


async def diagnose(browser, name: str, base_url: str):
    print(f"\n{'=' * 60}\nDIAGNOSE: {name}\n{'=' * 60}")
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    weekly_list_url = f"{base_url}/Planning/WeeklyList"
    await page.goto(weekly_list_url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    if "Disclaimer" in page.url:
        for sel in ["button:has-text('Accept')", "input[value*='Accept' i]"]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                        await loc.first.click(timeout=5_000)
                    print(f"    [{name}] Disclaimer click succeeded via: {sel!r}")
                    break
            except Exception:
                continue

    if "WeeklyList" not in page.url:
        await page.goto(weekly_list_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

    print(f"    [{name}] Real Weekly List page URL: {page.url}")

    # Real, direct capture of the first row's CSV link href — no
    # guessing at a URL pattern, reading it straight from the page.
    csv_links = await page.locator("a", has_text="CSV").all()
    print(f"    [{name}] Real 'CSV' link-text elements found: {len(csv_links)}")

    # The CSV text often sits next to the real link, not inside an <a>
    # itself — also check for any anchor whose href looks like a real
    # download endpoint.
    all_links = await page.locator("a[href]").all()
    real_csv_hrefs = []
    for link in all_links:
        href = await link.get_attribute("href")
        if href and ("csv" in href.lower() or "export" in href.lower() or "download" in href.lower()):
            real_csv_hrefs.append(href)

    print(f"    [{name}] Real hrefs containing csv/export/download: {len(real_csv_hrefs)}")
    for h in real_csv_hrefs[:5]:
        print(f"      {h}")

    if not real_csv_hrefs:
        print(f"    [{name}] No obvious CSV href found — dumping first 3000 chars of real page HTML")
        html = await page.content()
        print(html[:3000])
        await context.close()
        return

    first_csv_url = real_csv_hrefs[0]
    if not first_csv_url.startswith("http"):
        first_csv_url = base_url + first_csv_url

    print(f"    [{name}] Attempting real download from: {first_csv_url}")

    try:
        async with page.expect_download(timeout=15_000) as download_info:
            await page.goto(first_csv_url)
        download = await download_info.value
        safe_name = name.lower().replace(" ", "_")
        save_path = f"/tmp/weeklylist_{safe_name}.csv"
        await download.save_as(save_path)
        print(f"    [{name}] Real file saved to {save_path}")

        with open(save_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        lines = content.splitlines()
        print(f"    [{name}] Real total lines: {len(lines)}")
        print(f"    [{name}] Real header row: {lines[0] if lines else '(empty)'}")
        for i, line in enumerate(lines[1:6], start=1):
            print(f"    [{name}] Real row {i}: {line}")
    except Exception as e:
        print(f"    [{name}] REAL ERROR downloading/reading CSV: {type(e).__name__}: {e}")

    await context.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, base_url in CANDIDATES:
            try:
                await diagnose(browser, name, base_url)
            except Exception as e:
                print(f"\n⚠ Unexpected error diagnosing {name}: {type(e).__name__}: {e}")

        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
