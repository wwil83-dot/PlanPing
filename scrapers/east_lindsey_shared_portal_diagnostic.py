#!/usr/bin/env python3
"""
PlanFind — East Lindsey shared-portal filter diagnostic (2026-09-18).

Real, confirmed context: publicaccess.e-lindsey.gov.uk is a genuinely
shared "South & East Lincolnshire Councils Partnership" Idox portal
serving Boston, East Lindsey, and South Holland together (confirmed
via a direct screenshot showing all three councils' branding and
contact details on the same search page). East Lindsey's existing
config entry uses this URL with NO local-authority filter at all,
unlike Cambridge/South Cambridgeshire's confirmed
searchCriteria.localAuthority approach on their own shared portal.

This checks the real Advanced search form for a genuine local-
authority-style field (separate from the confirmed parish dropdown,
which filters by parish name, not council), and separately checks
whether East Lindsey's current unfiltered results actually include
applications whose real address falls in Boston or South Holland —
the same real mislabeling bug pattern already found and fixed
repeatedly elsewhere in this project for other shared-server
situations.
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

BASE_URL = "https://publicaccess.e-lindsey.gov.uk/online-applications"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print("=" * 60)
        print("STEP 1: Real Advanced search form field inventory")
        print("=" * 60)
        advanced_url = f"{BASE_URL}/search.do?action=advanced"
        await page.goto(advanced_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"Real Advanced page URL: {page.url}")
        print(f"Real Advanced page title: {await page.title()}\n")

        # Real, direct dump of every select dropdown's name attribute
        # and its label text — the most direct way to find a genuine
        # "local authority" style field distinct from the confirmed
        # parish dropdown.
        selects = await page.locator("select").all()
        print(f"Real <select> dropdowns found: {len(selects)}")
        for sel in selects:
            name = await sel.get_attribute("name")
            select_id = await sel.get_attribute("id")
            options = await sel.locator("option").all_text_contents()
            print(f"\n  name={name!r} id={select_id!r}")
            print(f"  real options (first 15): {options[:15]}")

        print("\n" + "=" * 60)
        print("STEP 2: Real unfiltered search — check for Boston/South Holland addresses")
        print("=" * 60)
        # Same real, standard Idox monthly-list flow already proven
        # elsewhere in this project — no local authority filter
        # applied, matching East Lindsey's CURRENT real config exactly.
        monthly_url = f"{BASE_URL}/search.do?action=monthlyList"
        await page.goto(monthly_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        month_select = page.locator("select[name='month']")
        if await month_select.count() > 0:
            submit = page.locator("input[type='submit'], button[type='submit']")
            if await submit.count() > 0:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    await submit.first.click(timeout=5_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass

                print(f"Real results URL: {page.url}")
                body_text = await page.locator("body").inner_text()

                # Real, direct text search for Boston/South Holland
                # place-name evidence in the actual unfiltered results
                # — the definitive way to confirm or rule out
                # mislabeling, rather than assuming either way.
                boston_matches = re.findall(r"[^\n]*\bBoston\b[^\n]*", body_text)
                sholland_matches = re.findall(r"[^\n]*\bSouth Holland\b[^\n]*", body_text)
                spalding_matches = re.findall(r"[^\n]*\bSpalding\b[^\n]*", body_text)  # South Holland's main town

                print(f"\nReal lines mentioning 'Boston': {len(boston_matches)}")
                for m in boston_matches[:5]:
                    print(f"  {m.strip()!r}")

                print(f"\nReal lines mentioning 'South Holland': {len(sholland_matches)}")
                for m in sholland_matches[:5]:
                    print(f"  {m.strip()!r}")

                print(f"\nReal lines mentioning 'Spalding': {len(spalding_matches)}")
                for m in spalding_matches[:5]:
                    print(f"  {m.strip()!r}")
            else:
                print("⚠ No real submit control found on monthly list form")
        else:
            print("⚠ No real month select found — monthly list form may differ")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
