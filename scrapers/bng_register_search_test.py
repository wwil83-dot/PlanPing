#!/usr/bin/env python3
"""
BNG register solvability check, round 2 (2026-09-27).

Confirmed from round 1: searching term=BGS returns the whole register
(395 sites), pagination at 100/page works with no overlap, and each
site has a predictable detail URL (/search/BGS-xxxxxxxxx) whose first
("Gain site") tab shows size, grid reference and the registering
body. Still unseen: the Habitat, Allocation and Amendments tabs —
the ones that would show whether units / allocations are public.

This (1) enumerates all sites via the BGS search and reports who
holds them and how precise the grid references are, and (2) opens
several sites, clicks each remaining tab and dumps what it shows.
"""
import asyncio
import re
from collections import Counter

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
RESULT_RE = re.compile(
    r"(BGS-\d+)\s*\nGrid reference:\s*([^\n]*)\nLocal Planning Authority or responsible body:\s*([^\n]*)"
)


async def wait_for_content(page):
    try:
        await page.wait_for_function(
            "() => !document.body.innerText.includes('Loading Message')",
            timeout=30_000,
        )
    except PlaywrightTimeout:
        print("    ⚠ still showing 'Loading Message...' after 30s")
    await asyncio.sleep(1)


async def load(page, url: str) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await wait_for_content(page)
    return await page.locator("body").inner_text()


def trim_detail(text: str) -> str:
    start = text.find("Gain site reference number")
    end = text.find("Start a new search")
    if start == -1:
        return text[:2500]
    return text[start:end if end != -1 else None][:2500]


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
        print("PART 1: enumerate the whole register via term=BGS")
        print("=" * 60)
        sites = {}
        for page_num in range(1, 6):
            body = await load(page, f"{BASE_URL}/search?term=BGS&page={page_num}&resultsPerPage=100")
            found = RESULT_RE.findall(body)
            print(f"  page {page_num}: {len(found)} sites parsed")
            for ref, grid, body_name in found:
                sites[ref] = (grid.strip(), body_name.strip())
            if not found:
                break
        print(f"  TOTAL distinct sites: {len(sites)}")

        bodies = Counter(b for _, b in sites.values())
        print(f"\n  distinct registering bodies: {len(bodies)}")
        print("  top 15 by number of sites:")
        for name, n in bodies.most_common(15):
            print(f"    {n:4d}  {name}")
        lpa_named = sum(n for name, n in bodies.items() if "LPA" in name)
        print(f"\n  sites whose body name contains 'LPA': {lpa_named}")
        print(f"  sites held by other bodies (companies/trusts): {len(sites) - lpa_named}")

        digit_lengths = Counter(
            len(re.sub(r"\D", "", g)) for g, _ in sites.values()
        )
        print(f"\n  grid reference digit counts (6=100m, 8=10m, 10=1m): {dict(digit_lengths)}")

        print("\n" + "=" * 60)
        print("PART 2: the Habitat / Allocation / Amendments tabs")
        print("=" * 60)
        sample_refs = ["BGS-140524001"] + [r for r in list(sites)[:4] if r != "BGS-140524001"]
        for ref in sample_refs[:4]:
            print(f"\n----- {ref} -----")
            await load(page, f"{BASE_URL}/search/{ref}")

            boundary = page.locator("a:has-text('Link to land boundary')")
            if await boundary.count() > 0:
                print(f"  land boundary link: {await boundary.first.evaluate('el => el.href')}")

            for tab in ["Habitat", "Allocation", "Amendments"]:
                target = page.locator(
                    f"[role='tab']:has-text('{tab}'), a:has-text('{tab}'), button:has-text('{tab}')"
                ).first
                if await target.count() == 0:
                    print(f"  [{tab}] tab element not found")
                    continue
                try:
                    await target.click(timeout=5_000)
                    await asyncio.sleep(1.5)
                    await wait_for_content(page)
                except Exception as e:
                    print(f"  [{tab}] click failed: {type(e).__name__}")
                    continue
                text = await page.locator("body").inner_text()
                print(f"\n  [{tab}] url after click: {page.url}")
                print(f"  [{tab}] content: {trim_detail(text)!r}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
