#!/usr/bin/env python3
"""
PlanFind — Barnet ODP register page 2+ diagnostic (2026-09-01).

odp_register_scraper.py's first two live runs both found real
pagination controls (1, 2, ..., 4, Next page) but ZERO <article>/<dl>
content on page 1 — even after an explicit 8-second wait for a real
<article> element, which ruled out simple timing as the cause. This
checks pages 2 and 4 directly: if THEY show real content while page 1
doesn't, that points to something specific about page 1's default
state rather than a fundamental structural mismatch with our parsing
logic.
"""
import asyncio
from datetime import datetime, timezone

from bs4 import BeautifulSoup
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

BASE_URL = "https://planningregister.org/barnet"


async def check_page(browser, page_num: int):
    print(f"\n{'=' * 70}")
    print(f"CHECKING PAGE {page_num}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    url = f"{BASE_URL}?page={page_num}&resultsPerPage=10&type=simple"
    print(f"URL: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        try:
            accept_btn = page.get_by_text("Accept analytics cookies", exact=True)
            if await accept_btn.count() > 0:
                await accept_btn.first.click(timeout=5_000)
                await asyncio.sleep(1)
        except Exception:
            pass

        try:
            await page.wait_for_selector("article", timeout=8_000)
            print("Real <article> element appeared!")
        except PlaywrightTimeout:
            print("No <article> element appeared within 8s")

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        articles = soup.find_all("article")
        dls = soup.find_all("dl")
        print(f"Real <article> count: {len(articles)}, real <dl> count: {len(dls)}")

        body_text = (await page.locator("body").inner_text())[:1500]
        print(f"Real body text (first 1500 chars): {body_text!r}")

        out_html = f"/tmp/barnet_page{page_num}_diagnostic.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved: {out_html}")

    except Exception as e:
        print(f"⚠ Error: {type(e).__name__}: {e!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Barnet page 2+ diagnostic\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await check_page(browser, 2)
        await check_page(browser, 4)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
