#!/usr/bin/env python3
"""
PlanFind — Arcus reconnaissance script, round 2.

Round 1 confirmed the "Planning Applications Weekly List" click reveals
real, clean, structured application data (Reference, Proposal, Site
Address, Date Valid, Status) — and crucially, a "Download as CSV" link is
present. If that CSV download hits a plain, parameterized URL, this could
mean Arcus scraping needs NO heavy Playwright/browser automation at all —
just a lightweight HTTP request per council, which would make it radically
simpler than Idox, not harder.

This script:
  1. Repeats round 1's navigation to the Weekly List.
  2. Logs every network request fired during that navigation and the CSV
     click, specifically looking for a plain GET URL (not an internal
     Salesforce /aura POST) that returns CSV/text content — this is the
     single most valuable thing to find in this whole exploration.
  3. Actually triggers the CSV download and saves the real file content.
  4. Separately tests "User Defined Weekly List" to see if it exposes a
     real date-range control (Idox's monthYearIndex equivalent) and
     whether that changes the browser URL to something reusable directly.

Run via GitHub Actions workflow_dispatch (see scrape.yml: arcus_recon job —
reuses the same job, this is recon_2.py as a distinct script).
"""
import asyncio
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

TARGET_URL = "https://ashfordboroughcouncil.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register"
TARGET_NAME = "Ashford Borough Council"

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

# Collected across the whole run so we can dump a clean summary at the end
network_log: list[dict] = []


def _log_request(request):
    # Skip the noise — static assets, fonts, tracking pixels. Keep anything
    # that looks like it might carry real data: XHR/fetch calls, or any URL
    # containing hints like csv/download/export/list/search.
    url = request.url
    if request.resource_type in ("xhr", "fetch") or any(
        kw in url.lower() for kw in ("csv", "download", "export", "list", "search", "aura")
    ):
        network_log.append({
            "method": request.method,
            "url": url,
            "resource_type": request.resource_type,
        })


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon round 2 — {TARGET_NAME}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS, accept_downloads=True)
        page = await context.new_page()
        page.on("request", _log_request)

        # --- Navigate to register-view, same as round 1 ---
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠ Initial navigation error: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(5)

        print(f"URL after initial load: {page.url}")

        # --- Click through to Weekly List (confirmed working in round 1) ---
        try:
            await page.get_by_text("Planning Applications Weekly List", exact=False).first.click(timeout=5_000)
            await asyncio.sleep(5)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Could not click Weekly List: {e}")
            await browser.close()
            return

        print(f"URL after Weekly List click: {page.url}")

        with open("/tmp/arcus_weekly_list_page.html", "w", encoding="utf-8") as f:
            f.write(await page.content())
        await page.screenshot(path="/tmp/arcus_weekly_list_page.png", full_page=True)

        # --- Try the CSV download ---
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await page.get_by_text("Download as CSV", exact=False).first.click()
            download = await download_info.value
            csv_path = "/tmp/arcus_weekly_list.csv"
            await download.save_as(csv_path)
            print(f"✓ CSV downloaded successfully: {csv_path}")
            print(f"  Suggested filename from server: {download.suggested_filename}")
            # Print the actual download URL if Playwright exposes it — this
            # is the single most valuable line of output in this whole
            # script if it's a plain, parameterized URL.
            print(f"  Download URL: {download.url}")
            with open(csv_path, encoding="utf-8", errors="replace") as f:
                preview = f.read(2000)
            print(f"\n--- CSV preview (first 2000 chars) ---\n{preview}\n")
        except Exception as e:
            print(f"⚠ CSV download failed or timed out: {e}")

        # --- Test User Defined Weekly List separately ---
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(3)

            await page.get_by_text("User Defined Weekly List", exact=False).first.click(timeout=5_000)
            await asyncio.sleep(5)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            print(f"\nURL after 'User Defined Weekly List' click: {page.url}")

            with open("/tmp/arcus_user_defined_list.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            await page.screenshot(path="/tmp/arcus_user_defined_list.png", full_page=True)

            # Count date-input-like elements to see if there's a real
            # date-range control (Idox's monthYearIndex equivalent)
            date_inputs = await page.locator("input[type='date'], input[type='text'][placeholder*='date' i], lightning-input").count()
            print(f"Date-input-like elements found: {date_inputs}")

        except Exception as e:
            print(f"⚠ Could not test User Defined Weekly List: {e}")

        await browser.close()

    # --- Dump the network log ---
    print(f"\n=== Network requests of interest ({len(network_log)}) ===")
    for entry in network_log:
        print(f"  [{entry['resource_type']}] {entry['method']} {entry['url']}")

    print("\n=== Recon round 2 complete ===")


if __name__ == "__main__":
    asyncio.run(main())
