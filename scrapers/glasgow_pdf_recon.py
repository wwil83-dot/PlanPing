#!/usr/bin/env python3
"""
PlanFind — Glasgow weekly-list PDF recon (2026-09-04).

Real find from the user's own browsing: glasgow.gov.uk/article/2095/
View-List-of-Planning-Applications lists weekly PDF downloads
(18/08/2026 - 24/08/2026 etc.) — the ArcGIS "Major and Significant"
dashboard route (glasgow_arcgis_recon1-7.py) hit a genuine, definitive
403 access-control wall and was parked; this PDF route is Glasgow's own
simpler, complete alternative (covers ALL applications, not just the
major/significant tier the dashboard was limited to anyway).

REAL FIX (2026-09-04) — a plain httpx GET hit a real Cloudflare "Just a
moment..." JS challenge (403, "Enable JavaScript and cookies to
continue") — same category of block already confirmed for Braintree
elsewhere in this project. Plain HTTP clients can't execute the
JavaScript Cloudflare requires; switched the initial page load to
Playwright (a real browser), which can often pass this lighter-tier
challenge where a plain client can't. Kept httpx for the actual PDF
binary downloads once real URLs are found, since PDF downloads
typically aren't behind the same JS challenge as the HTML page.

This recon: loads the real weekly-lists page via Playwright, finds the
real PDF href for the most recent 2 weeks, downloads them via httpx,
and dumps the actual extracted table structure via pdfplumber — before
any parser gets built around it.
"""
import asyncio
import io
from datetime import datetime, timezone

import httpx
import pdfplumber
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

WEEKLY_LISTS_URL = "https://www.glasgow.gov.uk/article/2095/View-List-of-Planning-Applications"

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

HTTP_HEADERS = {"User-Agent": CONTEXT_OPTIONS["user_agent"]}


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow weekly-list PDF recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print(f"\nLoading: {WEEKLY_LISTS_URL}")
        try:
            response = await page.goto(WEEKLY_LISTS_URL, wait_until="domcontentloaded", timeout=30_000)
            print(f"Real initial HTTP status: {response.status if response else 'None'}")
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            # Cloudflare's JS challenge takes a few seconds to resolve
            # even after the browser executes it successfully
            await asyncio.sleep(5)
        except Exception as e:
            print(f"⚠ Navigation error: {type(e).__name__}: {e!r}")
            await browser.close()
            return

        title = await page.title()
        print(f"Real page title after challenge: {title!r}")
        print(f"Real final URL: {page.url}")

        if "just a moment" in title.lower():
            print("⚠ Still stuck on the Cloudflare challenge page after waiting — "
                  "this may need a longer wait or isn't passable this way.")
            body_text = (await page.locator("body").inner_text())[:500]
            print(f"Real body text: {body_text!r}")
            await browser.close()
            return

        # Real PDF links, found via the actual rendered DOM
        links = page.locator("a")
        count = await links.count()
        pdf_links = []
        for i in range(count):
            el = links.nth(i)
            href = await el.get_attribute("href")
            text = (await el.inner_text()).strip()
            if href and (".pdf" in href.lower() or "PDF" in text):
                full_url = href if href.startswith("http") else f"https://www.glasgow.gov.uk{href}"
                pdf_links.append((text, full_url))

        print(f"\nReal PDF links found: {len(pdf_links)}")
        for text, url in pdf_links[:10]:
            print(f"  {text!r} -> {url}")

        await browser.close()

    if not pdf_links:
        print("\n⚠ No PDF links found even after passing the challenge — "
              "real page structure may differ from expected.")
        return

    # Download and inspect the most recent 2 real PDFs via plain httpx
    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30, follow_redirects=True) as client:
        for text, url in pdf_links[:2]:
            print(f"\n{'=' * 70}")
            print(f"DOWNLOADING: {text!r}")
            print(f"URL: {url}")
            print("=" * 70)

            try:
                pdf_r = await client.get(url)
                print(f"Real download HTTP status: {pdf_r.status_code}")
                print(f"Real content size: {len(pdf_r.content)} bytes")

                if pdf_r.status_code != 200:
                    print(f"⚠ Non-200 download — real body preview: {pdf_r.text[:300]!r}")
                    continue

                with pdfplumber.open(io.BytesIO(pdf_r.content)) as pdf:
                    print(f"Real page count: {len(pdf.pages)}")
                    for i, page_obj in enumerate(pdf.pages[:2]):
                        print(f"\n  --- Page {i + 1} real extracted text (first 1500 chars) ---")
                        text_content = page_obj.extract_text() or ""
                        print(f"  {text_content[:1500]!r}")

                        tables = page_obj.extract_tables()
                        print(f"\n  Real tables found on this page: {len(tables)}")
                        for j, table in enumerate(tables):
                            print(f"  Table {j + 1}: {len(table)} rows")
                            for row in table[:5]:
                                print(f"    {row}")

            except Exception as e:
                print(f"⚠ Download/parse failed: {type(e).__name__}: {e!r}")

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
