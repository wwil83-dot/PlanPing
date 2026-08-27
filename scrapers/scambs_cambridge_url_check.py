#!/usr/bin/env python3
"""
PlanFind — South Cambridgeshire URL currency check (2026-08-27).

Real, confirmed: South Cambridgeshire's existing active config entry
uses planning.scambs.gov.uk, a genuinely different domain from the
Greater Cambridge Shared Planning system just found
(applications.greatercambridgeplanning.org), which explicitly
describes itself as a joint service for Cambridge City AND South
Cambridgeshire together. Checking whether the existing URL still
genuinely works before deciding whether to update it or add a
separate, new entry.
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

TARGETS = [
    ("South Cambridgeshire (existing config URL)",
     "https://planning.scambs.gov.uk/online-applications/"),
    ("Cambridge City (existing config URL)", None),  # filled below once found
]


async def check_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"CHECK: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    print(f"  Real HTTP status: {response.status if response else None}")
    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:500]
    except Exception:
        pass
    print(f"  Real visible body text (first 500 chars): {body_text!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] South Cambridgeshire URL currency check\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await check_one(browser, "South Cambridgeshire (existing config URL)",
                         "https://planning.scambs.gov.uk/online-applications/")
        await check_one(browser, "Cambridge City (existing config URL)",
                         "https://idox.cambridge.gov.uk/online-applications/")

        await browser.close()

    print(f"\n{'=' * 70}")
    print("CHECK COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
