#!/usr/bin/env python3
"""
PlanFind — Highland Council weekly-list PDF recon (2026-09-04).

Real find from the user's own browsing: highland.gov.uk/downloads/
download/1137/weekly-list-of-planning-applications — a real alternative
to Highland's own blocked Idox instance (never directly tested for
blocking, but the user is treating it the same way Gloucester/Edinburgh
were: assume blocked, look for an alternate route rather than spend
more cycles confirming a block that's very likely the same as its
peers).

NOTABLE: this URL pattern (/downloads/download/NNNN/slug) is IDENTICAL
in shape to Edinburgh's own weekly-list page (edinburgh.gov.uk/
downloads/download/14461/planning-weekly-lists-part-a) — likely the
same shared council-website CMS platform used across multiple Scottish
councils. If this one works cleanly, the same approach may generalise.

Using Playwright from the start this time (not plain httpx) — Glasgow's
equivalent page load needed a real browser to pass Cloudflare's JS
challenge, and its PDF downloads needed a REAL page navigation
specifically (not even Playwright's own request API was enough) to
finally get past a stricter WAF rule on the file-serving path. Starting
with the same real-navigation approach here from the outset rather than
repeating the same trial-and-error.

This recon: loads the real weekly-list page via Playwright, finds the
real PDF href for the most recent 2 weeks, downloads them via real page
navigations in the same browser session, and dumps the actual extracted
table structure via pdfplumber — before any parser gets built around it.
"""
import asyncio
import io
from datetime import datetime, timezone

import pdfplumber
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

WEEKLY_LIST_URL = "https://www.highland.gov.uk/downloads/download/1137/weekly-list-of-planning-applications"

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


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Highland Council weekly-list PDF recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print(f"\nLoading: {WEEKLY_LIST_URL}")
        try:
            response = await page.goto(WEEKLY_LIST_URL, wait_until="domcontentloaded", timeout=30_000)
            print(f"Real initial HTTP status: {response.status if response else 'None'}")
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(3)
        except Exception as e:
            print(f"⚠ Navigation error: {type(e).__name__}: {e!r}")
            await browser.close()
            return

        title = await page.title()
        print(f"Real page title: {title!r}")
        print(f"Real final URL: {page.url}")

        if "just a moment" in title.lower():
            print("⚠ Hit a Cloudflare-style JS challenge on the main page itself.")
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
                full_url = href if href.startswith("http") else f"https://www.highland.gov.uk{href}"
                pdf_links.append((text, full_url))

        print(f"\nReal PDF links found: {len(pdf_links)}")
        for text, url in pdf_links[:10]:
            print(f"  {text!r} -> {url}")

        if not pdf_links:
            print("\n⚠ No PDF links found — real page structure may differ from expected.")
            body_text = (await page.locator("body").inner_text())[:1500]
            print(f"Real body text (first 1500 chars): {body_text!r}")
            await browser.close()
            return

        # REAL FIX (2026-09-04) — the first attempt treated this like
        # Glasgow's file-serving path (a real page.goto() navigation)
        # and got a real, informative error: "Download is starting".
        # That's NOT a block — Highland's server sends a real
        # Content-Disposition: attachment header, which makes the
        # browser trigger an actual file download rather than
        # navigating normally. Genuinely much simpler than Glasgow's
        # Cloudflare situation — no WAF/challenge at all here, just a
        # different technical detail needing Playwright's proper
        # download-handling API instead of a plain navigation.
        for text, url in pdf_links[:2]:
            print(f"\n{'=' * 70}")
            print(f"DOWNLOADING: {text!r}")
            print(f"URL: {url}")
            print("=" * 70)

            try:
                async with page.expect_download(timeout=30_000) as download_info:
                    try:
                        await page.goto(url, timeout=30_000)
                    except Exception:
                        pass  # the goto itself is expected to "fail" once the download starts
                download = await download_info.value
                download_path = await download.path()
                print(f"Real download saved to: {download_path}")

                with open(download_path, "rb") as f:
                    content = f.read()
                print(f"Real content size: {len(content)} bytes")

                with pdfplumber.open(io.BytesIO(content)) as pdf:
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

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
