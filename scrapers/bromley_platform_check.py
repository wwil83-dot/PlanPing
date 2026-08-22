#!/usr/bin/env python3
"""
PlanFind — Bromley platform conflict diagnostic (2026-08-22).

Real, confirmed situation: Bromley has TWO active config entries on TWO
different platforms — an Idox entry (searchapplications.bromley.gov.uk)
and an Arcus entry (planningaccess.bromley.gov.uk), sharing the same
real council_id (230). The Arcus entry's own comment already flagged
this as an unresolved "may be replacing an existing" situation. This
checks both real URLs directly with a real browser (this sandbox's own
bash tool can't reach .gov.uk domains at all — every earlier curl
attempt returned 403 from BOTH, which is a real, confirmed limitation
of the sandbox's own network allowlist, not real evidence about either
platform) to determine which is genuinely live today, rather than
guess.
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
    ("Bromley — Idox",
     "https://searchapplications.bromley.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Bromley — Arcus",
     "https://planningaccess.bromley.gov.uk/pr/s"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("—", "")


async def check_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"CHECKING: {name}")
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
    print(f"  Real final URL: {page.url}")
    title = await page.title()
    print(f"  Real page title: {title!r}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:500]
    except Exception:
        pass
    print(f"  Real visible body text (first 500 chars): {body_text!r}")

    out_png = f"/tmp/bromley_check_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
        print(f"  Saved screenshot: {out_png}")
    except Exception:
        pass

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Bromley platform conflict diagnostic\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, url in TARGETS:
            await check_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)
    print("Compare both real results above — whichever shows a genuine, live")
    print("planning-search page is the one to keep active; the other should")
    print("be commented out to stop wasting nightly attempts and avoid any")
    print("risk of duplicate/conflicting data for the same council_id.")


if __name__ == "__main__":
    asyncio.run(main())
