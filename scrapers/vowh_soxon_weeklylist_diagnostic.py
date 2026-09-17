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

    # REAL FIX (round 3) — confirmed via the actual page HTML: this is
    # NOT a plain <a href> link at all. It's a JS-triggered
    # <button class="getWeeklyListCSV" data-date="...">, explaining
    # why every href-based search found nothing — there's no URL to
    # find, only a click handler. Finding and clicking the real first
    # button directly.
    csv_buttons = await page.locator("button.getWeeklyListCSV").all()
    print(f"    [{name}] Real getWeeklyListCSV buttons found: {len(csv_buttons)}")

    if not csv_buttons:
        print(f"    [{name}] No real CSV button found — dumping the real content area")
        try:
            main_content = await page.locator("main, .container, body").first.inner_html()
            print(main_content[:4000])
        except Exception as e:
            print(f"    [{name}] Could not extract content area: {type(e).__name__}: {e}")
        await context.close()
        return

    first_button = csv_buttons[0]
    real_date = await first_button.get_attribute("data-date")
    print(f"    [{name}] Real first button's data-date: {real_date!r} — clicking it")

    try:
        async with page.expect_download(timeout=15_000) as download_info:
            await first_button.click(timeout=5_000)
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
    return


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
