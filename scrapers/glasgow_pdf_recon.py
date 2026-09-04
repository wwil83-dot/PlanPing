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

This recon: fetches the real weekly-lists page, finds the real PDF
href for the most recent 2 weeks, downloads them, and dumps the actual
extracted table structure via pdfplumber — before any parser gets
built around it.
"""
import asyncio
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

WEEKLY_LISTS_URL = "https://www.glasgow.gov.uk/article/2095/View-List-of-Planning-Applications"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow weekly-list PDF recon\n")

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30, follow_redirects=True) as client:
        print(f"Fetching: {WEEKLY_LISTS_URL}")
        try:
            r = await client.get(WEEKLY_LISTS_URL)
            print(f"Real HTTP status: {r.status_code}")
            print(f"Real final URL: {r.url}")
        except Exception as e:
            print(f"⚠ Request failed: {type(e).__name__}: {e!r}")
            return

        soup = BeautifulSoup(r.text, "html.parser")

        # Find all real PDF links on the page
        pdf_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if ".pdf" in href.lower() or "PDF" in text:
                full_url = href if href.startswith("http") else f"https://www.glasgow.gov.uk{href}"
                pdf_links.append((text, full_url))

        print(f"\nReal PDF links found: {len(pdf_links)}")
        for text, url in pdf_links[:10]:
            print(f"  {text!r} -> {url}")

        if not pdf_links:
            print("\n⚠ No PDF links found — real page structure may differ from expected.")
            print(f"Real body text (first 1500 chars): {soup.get_text()[:1500]!r}")
            return

        # Download and inspect the most recent 2 real PDFs
        import pdfplumber
        import io

        for text, url in pdf_links[:2]:
            print(f"\n{'=' * 70}")
            print(f"DOWNLOADING: {text!r}")
            print(f"URL: {url}")
            print("=" * 70)

            try:
                pdf_r = await client.get(url)
                print(f"Real download HTTP status: {pdf_r.status_code}")
                print(f"Real content size: {len(pdf_r.content)} bytes")

                with pdfplumber.open(io.BytesIO(pdf_r.content)) as pdf:
                    print(f"Real page count: {len(pdf.pages)}")
                    for i, page in enumerate(pdf.pages[:2]):
                        print(f"\n  --- Page {i + 1} real extracted text (first 1500 chars) ---")
                        text_content = page.extract_text() or ""
                        print(f"  {text_content[:1500]!r}")

                        tables = page.extract_tables()
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
