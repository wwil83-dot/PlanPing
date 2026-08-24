#!/usr/bin/env python3
"""
PlanFind — Cherwell/North Warwickshire detail page recon (2026-08-24).

Real, confirmed gap from esl_scraper.py's first live run with the new
received-date fetch: Eden/South Lakeland, Wychavon, and Malvern Hills
all found a real match, but Cherwell and North Warwickshire both
silently found zero matches, despite Cherwell sharing the exact same
underlying platform as the 3 that worked. Checking both real detail
pages directly with already-confirmed real references from earlier
tonight's evidence, rather than assume either is fine.
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

# Real, already-confirmed references reused directly from earlier
# tonight's evidence (search_advanced_family_recon.py's Cherwell run,
# and the North Warwickshire screenshot)
TARGETS = [
    ("Cherwell", "https://planningregister.cherwell.gov.uk/Planning/Display/26/01844/AGN"),
    ("North Warwickshire", "https://planning.northwarks.gov.uk/Planning/Display?applicationNumber=2026%2F0654%2FTRE"),
]


async def check_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    print(f"  Real HTTP status: {response.status if response else None}")
    print(f"  Real final URL: {page.url}")

    body_text = ""
    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        pass

    print(f"\n  Real visible body text (first 3000 chars):\n{body_text[:3000]!r}")

    import re
    date_pattern = re.compile(r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b")
    date_matches = date_pattern.findall(body_text)
    print(f"\n  Real date-shaped strings found anywhere on the page: {date_matches}")

    received_check = re.search(r"received", body_text, re.I)
    if received_check:
        idx = received_check.start()
        print(f"\n  Real context around the word 'received': "
              f"{body_text[max(0, idx-30):idx+60]!r}")
    else:
        print(f"\n  ⚠ The word 'received' does not appear anywhere on this page at all")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Cherwell/North Warwickshire detail page recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, url in TARGETS:
            await check_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
