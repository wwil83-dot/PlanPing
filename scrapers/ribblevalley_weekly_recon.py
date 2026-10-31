#!/usr/bin/env python3
"""
PlanFind - Ribble Valley weekly-list PDF recon (2026-10-31).

Ribble Valley switched off its old planning search (webportal.ribblevalley.gov.uk) and replaced it with a reference/address
search that cannot be listed by date. The council still publishes two weekly PDF lists on its own website:
  - registered: https://www.ribblevalley.gov.uk/downloads/download/235/weekly-lists-of-planning-applications-registered
  - decisions:  https://www.ribblevalley.gov.uk/downloads/download/249/weekly-lists-of-planning-decisions
Same CMS download-page pattern as Highland and Edinburgh. This recon loads both pages with Playwright, finds the PDF links,
downloads the newest two of each (real browser download, as for Highland) and dumps the extracted text and tables, so a parser
can be written around the real layout. It writes nothing to the database.
"""
import asyncio
import io
import os
from datetime import datetime, timezone

import pdfplumber
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

PAGES = {
    "registered": "https://www.ribblevalley.gov.uk/downloads/download/235/weekly-lists-of-planning-applications-registered",
    "decisions": "https://www.ribblevalley.gov.uk/downloads/download/249/weekly-lists-of-planning-decisions",
}
BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,
    "accept_downloads": True,
}
OUT = "/tmp/rv_weekly"


async def main():
    os.makedirs(OUT, exist_ok=True)
    print(f"[{datetime.now(timezone.utc).isoformat()}] Ribble Valley weekly-list PDF recon\n")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()
        for kind, url in PAGES.items():
            print("=" * 78)
            print(f"{kind.upper()}: {url}")
            print("=" * 78)
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                print(f"HTTP status: {resp.status if resp else None}")
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(2)
            except Exception as e:
                print(f"⚠ Navigation error: {type(e).__name__}: {e!r}")
                continue
            print(f"Title: {await page.title()!r}   URL: {page.url}")

            links, seen = [], set()
            anchors = page.locator("a")
            for i in range(await anchors.count()):
                el = anchors.nth(i)
                href = await el.get_attribute("href") or ""
                text = " ".join((await el.inner_text()).split())
                if not href or href in seen:
                    continue
                if "/downloads/file" in href or ".pdf" in href.lower():
                    seen.add(href)
                    links.append((text, href if href.startswith("http") else "https://www.ribblevalley.gov.uk" + href))
            print(f"\nPDF/file links found: {len(links)}")
            for text, href in links[:12]:
                print(f"  {text!r} -> {href}")
            if not links:
                print("⚠ none found; every link on the page:")
                for i in range(min(await anchors.count(), 60)):
                    el = anchors.nth(i)
                    print(f"   {(await el.get_attribute('href'))!r}  {' '.join((await el.inner_text()).split())[:50]!r}")
                continue

            for n, (text, href) in enumerate(links[:2]):
                print(f"\n--- DOWNLOAD {n + 1}: {text!r} ---")
                try:
                    async with page.expect_download(timeout=40_000) as info:
                        try:
                            await page.goto(href, timeout=40_000)
                        except Exception:
                            pass            # a Content-Disposition download makes goto 'fail'
                    dl = await info.value
                    path = await dl.path()
                    data = open(path, "rb").read()
                    keep = f"{OUT}/{kind}_{n + 1}.pdf"
                    open(keep, "wb").write(data)
                    print(f"saved {len(data)} bytes -> {keep}")
                    with pdfplumber.open(io.BytesIO(data)) as pdf:
                        print(f"pages: {len(pdf.pages)}")
                        for pi, pg in enumerate(pdf.pages[:2]):
                            txt = pg.extract_text() or ""
                            print(f"\n  [page {pi + 1}] text (first 2500 chars):\n{txt[:2500]}")
                            tables = pg.extract_tables()
                            print(f"\n  [page {pi + 1}] tables: {len(tables)}")
                            for ti, tb in enumerate(tables[:2]):
                                print(f"    table {ti + 1}: {len(tb)} rows")
                                for row in tb[:6]:
                                    print(f"      {row}")
                except Exception as e:
                    print(f"⚠ download/parse failed: {type(e).__name__}: {e!r}")
        await browser.close()
    print("\nRECON COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
