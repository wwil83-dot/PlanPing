#!/usr/bin/env python3
"""
BNG register solvability check (2026-09-27).

Two real, still-unanswered questions before deciding whether a BNG
broker product is buildable:

1. ENUMERATION — the register's search has no "list everything"
   option. Which search strategies (reference prefix, habitat terms)
   return large result sets, and does pagination via the confirmed
   URL parameters (term, page, resultsPerPage) work?
2. DETAIL PAGE — what does a single site's own page actually expose
   (habitat units, allocations, location, status)? This decides
   whether the data a broker needs is public at all.

Reuses the confirmed working search flow: results load client-side
(a "Loading Message..." placeholder first), so every page waits for
that text to genuinely disappear before reading anything.
"""
import asyncio
import re

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

BASE_URL = "https://environment.data.gov.uk/biodiversity-net-gain"


async def wait_for_content(page):
    try:
        await page.wait_for_function(
            "() => !document.body.innerText.includes('Loading Message')",
            timeout=30_000,
        )
    except PlaywrightTimeout:
        print("    ⚠ still showing 'Loading Message...' after 30s")
    await asyncio.sleep(1)


async def open_search(page, term: str, page_num: int = 1, per_page: int = 10) -> str:
    url = f"{BASE_URL}/search?term={term}&page={page_num}&resultsPerPage={per_page}"
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await wait_for_content(page)
    return await page.locator("body").inner_text()


def result_count(body_text: str):
    if "No search results found" in body_text:
        return 0
    m = re.search(r"(\d[\d,]*)\s+results?\b", body_text)
    return int(m.group(1).replace(",", "")) if m else None


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        accept = page.locator("button:has-text('Accept all cookies')")
        if await accept.count() > 0:
            await accept.first.click(timeout=5_000)
            print("Real cookie banner accepted\n")

        print("=" * 60)
        print("PART 1: which search terms return large result sets?")
        print("=" * 60)
        terms = ["BGS", "BGS-", "grassland", "woodland", "heathland", "wetland",
                 "hedgerow", "scrub", "cropland", "orchard", "coastal", "urban",
                 "LPA", "Limited"]
        for term in terms:
            body = await open_search(page, term)
            print(f"  {term!r}: {result_count(body)} results")

        print("\n" + "=" * 60)
        print("PART 2: does pagination work at 100 per page? ('grassland')")
        print("=" * 60)
        body = await open_search(page, "grassland", page_num=1, per_page=100)
        refs_p1 = re.findall(r"BGS-\d+", body)
        print(f"  page 1: {len(refs_p1)} references, count line: {result_count(body)}")
        body = await open_search(page, "grassland", page_num=2, per_page=100)
        refs_p2 = re.findall(r"BGS-\d+", body)
        print(f"  page 2: {len(refs_p2)} references")
        print(f"  overlap between page 1 and 2: {len(set(refs_p1) & set(refs_p2))}")

        print("\n" + "=" * 60)
        print("PART 3: what does a single site's detail page expose?")
        print("=" * 60)
        body = await open_search(page, "grassland", per_page=10)
        links = page.locator("a:has-text('BGS-')")
        link_count = await links.count()
        print(f"  result links containing a BGS- reference: {link_count}")

        detail_href = None
        if link_count > 0:
            detail_href = await links.first.evaluate("el => el.href")
            print(f"  first link href: {detail_href}")
        else:
            print("  no anchor links — listing other clickable candidates")
            for sel in ["a", "button"]:
                els = page.locator(f"main {sel}, body {sel}")
                n = await els.count()
                for i in range(min(n, 15)):
                    txt = (await els.nth(i).inner_text()).strip()[:60]
                    href = await els.nth(i).get_attribute("href")
                    print(f"    <{sel}> text={txt!r} href={href!r}")

        if detail_href:
            await page.goto(detail_href, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await wait_for_content(page)
            print(f"\n  detail page URL: {page.url}")
            print(f"  detail page title: {await page.title()}")
            detail_text = await page.locator("body").inner_text()
            print(f"\n  detail page text (first 6000 chars):")
            print(repr(detail_text[:6000]))

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
