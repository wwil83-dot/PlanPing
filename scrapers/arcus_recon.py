#!/usr/bin/env python3
"""
PlanFind — Arcus reconnaissance tool (final version).

Consolidates everything learned across 12 rounds of live reconnaissance
(2026-07-13) into a single, reusable tool for onboarding NEW Arcus
councils. Run this against any candidate council's register-view URL
before adding it to arcus_councils.py — do not guess link text, every
council checked so far has worded its "Weekly lists" links differently.

Usage:
  ARCUS_TARGET_URL="https://example.my.site.com/pr/s" python arcus_recon.py

What this does:
  1. Loads the council's register-view homepage with proper waits for
     Salesforce Lightning to finish rendering (this alone trips up naive
     scraping — see round 1's "Loading... Sorry to interrupt" findings).
  2. Screenshots the homepage — READ THIS SCREENSHOT to find the real
     "Weekly lists" section link text. Do not trust automated text
     matching; every council words this differently.
  3. Attempts Advanced Search as a secondary check (select Category via
     Lightning combobox, fill a 14-day date range, click the LAST "Search"
     button on the page — see round 12 for why "last" not "first" matters)
     and reports whether real results rendered, as a sanity check that
     this council's Arcus instance is alive and behaves consistently with
     the three already confirmed working.

Output: screenshots + HTML saved as a workflow artifact (see scrape.yml:
arcus_recon job). Read the homepage screenshot, find the real weekly-list
link text by eye, then add the council to arcus_councils.py.
"""
import asyncio
import os
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

TARGET_URL = os.environ.get("ARCUS_TARGET_URL") or (
    "https://ashfordboroughcouncil.my.site.com/pr/s"  # sensible default —
    # a known-working council, so this tool is never a no-op if run
    # without config. Using `or` rather than .get()'s default param
    # deliberately — GitHub Actions sets a blank workflow_dispatch input to
    # an EMPTY STRING, not an absent env var, so .get()'s default would
    # never actually trigger without this.
)
TARGET_NAME = os.environ.get("ARCUS_TARGET_NAME") or "Recon target"

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,  # required — some Arcus custom domains
                                    # have certificates Chromium rejects
                                    # by default (confirmed: Manchester)
    "accept_downloads": True,
}


async def main():
    register_url = f"{TARGET_URL.rstrip('/')}/register-view?c__r=Arcus_BE_Public_Register"
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon — {TARGET_NAME}")
    print(f"Target URL: {register_url}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        # --- Load homepage with proper Lightning wait ---
        try:
            await page.goto(register_url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠ Initial navigation error: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            print("⚠ networkidle wait timed out (common for Lightning apps, not necessarily a problem)")
        await asyncio.sleep(5)

        homepage_html = await page.content()
        print(f"Homepage HTML length: {len(homepage_html)} chars")
        print(f"Contains 'Weekly lists' heading: {'Weekly lists' in homepage_html}")
        print(f"Contains 'Advanced search' link: {'Advanced search' in homepage_html}")

        with open("/tmp/arcus_recon_homepage.html", "w", encoding="utf-8") as f:
            f.write(homepage_html)
        await page.screenshot(path="/tmp/arcus_recon_homepage.png", full_page=True)
        print("\n>>> Saved /tmp/arcus_recon_homepage.png — READ THIS to find the "
              "real 'Weekly lists' link text for this council. <<<\n")

        # --- Sanity check: try Advanced Search, same proven sequence as
        # the production scraper's design (though the production scraper
        # itself uses the weekly-list route, not Advanced Search — this is
        # purely a check that this council's Arcus instance is alive and
        # behaves consistently with the three already confirmed). ---
        try:
            loc = page.get_by_text("Advanced search", exact=False)
            if await loc.count() > 0:
                await loc.first.click(timeout=5_000)
                await asyncio.sleep(5)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass

                # Category (best-effort — Lightning combobox pattern)
                try:
                    category_field = page.get_by_label("Category", exact=False)
                    if await category_field.count() > 0:
                        await category_field.first.click(timeout=5_000)
                        await asyncio.sleep(1)
                        option = page.get_by_role("option", name="Planning Applications", exact=False)
                        if await option.count() > 0:
                            await option.first.click(timeout=5_000)
                            print("✓ Category combobox: selected 'Planning Applications'")
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                        await asyncio.sleep(3)
                except Exception:
                    print("⚠ Category selection not confirmed (may not be present on this council)")

                # Dates (best-effort)
                from datetime import date, timedelta
                today = date.today()
                two_weeks_ago = today - timedelta(days=14)
                for label in ["Valid date from", "Date from", "Received date from"]:
                    try:
                        field = page.get_by_label(label, exact=False)
                        if await field.count() > 0:
                            await field.first.fill(two_weeks_ago.strftime("%d/%m/%Y"), timeout=5_000)
                            await field.first.press("Tab")
                            break
                    except Exception:
                        continue
                for label in ["Valid date to", "Date to", "Received date to"]:
                    try:
                        field = page.get_by_label(label, exact=False)
                        if await field.count() > 0:
                            await field.first.fill(today.strftime("%d/%m/%Y"), timeout=5_000)
                            await field.first.press("Tab")
                            break
                    except Exception:
                        continue

                # Click the LAST "Search" button — round 12's critical fix.
                # Multiple "Search" buttons commonly exist on these pages
                # (a quick-search box near the top, plus the Advanced
                # Search form's own submit button); the form's real submit
                # button is reliably the last one in document order.
                search_buttons = page.get_by_role("button", name="Search", exact=False)
                btn_count = await search_buttons.count()
                if btn_count > 0:
                    await search_buttons.last.click(timeout=5_000)
                    await asyncio.sleep(5)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass

                    results_html = await page.content()
                    has_results = "Application Reference" in results_html or results_html.count("Reference") > 3
                    print(f"\nAdvanced Search sanity check: "
                          f"{'✓ real results rendered' if has_results else '⚠ no results detected'}")

                    with open("/tmp/arcus_recon_advanced_search_results.html", "w", encoding="utf-8") as f:
                        f.write(results_html)
                    await page.screenshot(path="/tmp/arcus_recon_advanced_search_results.png", full_page=True)
                else:
                    print("\n⚠ No 'Search' button found — could not complete Advanced Search sanity check")
            else:
                print("\n⚠ No 'Advanced search' link found on this council's homepage")
        except Exception as e:
            print(f"\n⚠ Advanced Search sanity check failed: {e}")

        await browser.close()

    print("\n=== Recon complete ===")
    print("Next step: download the workflow artifact, look at "
          "arcus_recon_homepage.png, and find the REAL 'Weekly lists' "
          "link text by eye before adding this council to arcus_councils.py.")


if __name__ == "__main__":
    asyncio.run(main())
