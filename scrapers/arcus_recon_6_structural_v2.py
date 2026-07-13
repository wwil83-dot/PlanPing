#!/usr/bin/env python3
"""
PlanFind — Arcus reconnaissance script, round 6: structural link-finding.

Round 4 revealed two separate, unrelated problems:

  1. Epping Forest genuinely uses different link wording than Ashford
     ("Planning Applications Validated this week" / "...Decided this week"
     rather than a single "Planning Applications Weekly List" link) — so
     hardcoded text matching doesn't generalise across councils.

  2. Manchester's custom domain (arcusbe.manchester.gov.uk) has a
     certificate that Chromium rejects by default. This was a bug in the
     PREVIOUS recon scripts, not a real finding about Manchester — they
     were missing ignore_https_errors=True, which the working Idox
     scraper already sets correctly for its own council list. Fixed here.

This round tries a STRUCTURAL approach instead of guessing exact wording:
both Ashford and Epping Forest render an identical "Weekly lists" section
heading with links directly beneath it. Rather than hardcode a specific
label, find that heading and click whatever link(s) appear near it. If
this generalises, it's the real portable strategy for the production
scraper — much like how Idox's "monthlyList" URL action name stays
consistent even though council branding/wording varies on top of it.
"""
import asyncio
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

TEST_TARGETS = [
    {
        "name": "Epping Forest District Council",
        "url": "https://eppingforestdc.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register",
    },
    {
        "name": "Manchester City Council",
        "url": "https://arcusbe.manchester.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register",
    },
]

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    # FIX: this was missing in rounds 2-4, which is why Manchester silently
    # failed to load at all — its custom domain has a certificate Chromium
    # rejects by default. The working Idox scraper already sets this.
    "ignore_https_errors": True,
}


async def test_council(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"Testing: {name}")
    print(f"{'=' * 70}")
    print(f"Starting URL: {url}\n")

    context = await browser.new_context(**CONTEXT_OPTIONS, accept_downloads=True)
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(2)
    except Exception as e:
        print(f"⚠ Initial navigation error: {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        print("⚠ networkidle wait timed out (common for Lightning apps)")
    await asyncio.sleep(5)

    homepage_html = await page.content()
    print(f"Homepage HTML length after wait: {len(homepage_html)} chars")
    print(f"Contains 'Weekly lists' heading: {'Weekly lists' in homepage_html}")

    with open(f"/tmp/arcus_r6_{name.replace(' ', '_')}_00_homepage.html", "w", encoding="utf-8") as f:
        f.write(homepage_html)
    await page.screenshot(path=f"/tmp/arcus_r6_{name.replace(' ', '_')}_00_homepage.png", full_page=True)

    # --- STRUCTURAL approach, v2: round 6's "search within the immediate
    # ancestor container" found 0 links on BOTH councils — the container
    # picked was too narrow (likely just a tight wrapper around the heading
    # text itself, with the real links living in sibling elements next to
    # it, not descendants of it). This version uses XPath's `following::`
    # axis instead, which finds elements LATER IN DOCUMENT ORDER regardless
    # of how deeply nested or where exactly they sit relative to the
    # heading — much more robust to unknown/varying HTML structure. ---
    clicked = False
    clicked_text = None
    try:
        heading_count = await page.get_by_text("Weekly lists", exact=False).count()
        if heading_count > 0:
            # Grab the next handful of <a> elements that appear anywhere
            # after the "Weekly lists" text in document order.
            following_links = page.locator(
                "xpath=//*[contains(text(), 'Weekly lists')]/following::a[position() <= 8]"
            )
            link_count = await following_links.count()
            print(f"\nLinks found following 'Weekly lists' heading (document order): {link_count}")

            for i in range(link_count):
                try:
                    link_text = await following_links.nth(i).inner_text()
                    print(f"  Link {i}: '{link_text.strip()}'")
                except Exception:
                    print(f"  Link {i}: (could not read text)")

            if link_count > 0:
                clicked_text = (await following_links.first.inner_text()).strip()
                await following_links.first.click(timeout=5_000)
                clicked = True
                print(f"\n✓ Clicked first link found via following:: axis: '{clicked_text}'")
        else:
            print("\n⚠ Could not find a 'Weekly lists' heading at all on this council's homepage.")
    except Exception as e:
        print(f"\n⚠ Structural link-finding failed: {e}")

    if not clicked:
        await context.close()
        return

    await asyncio.sleep(5)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    results_html = await page.content()
    print(f"\nPage title after click: '{await page.title()}'")
    print(f"HTML length after click: {len(results_html)} chars")

    indicators = {
        "Application Reference": results_html.count("Application Reference"),
        "Site Address": results_html.count("Site Address"),
        "Download as CSV": results_html.count("Download as CSV"),
        "Week Commencing": results_html.count("Week Commencing"),
        "Showing": results_html.count("Showing"),
    }
    for k, v in indicators.items():
        print(f"  Count of '{k}': {v}")

    with open(f"/tmp/arcus_r6_{name.replace(' ', '_')}_01_after_click.html", "w", encoding="utf-8") as f:
        f.write(results_html)
    await page.screenshot(path=f"/tmp/arcus_r6_{name.replace(' ', '_')}_01_after_click.png", full_page=True)

    if indicators["Application Reference"] > 0 or indicators["Download as CSV"] > 0:
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await page.get_by_text("Download as CSV", exact=False).first.click()
            download = await download_info.value
            csv_path = f"/tmp/arcus_r6_{name.replace(' ', '_')}.csv"
            await download.save_as(csv_path)
            with open(csv_path, encoding="utf-8", errors="replace") as f:
                preview = f.read(600)
            print(f"\n✓✓✓ CSV download succeeded via STRUCTURAL click approach on {name}!")
            print(f"--- CSV preview (first 600 chars) ---\n{preview}")
        except Exception as e:
            print(f"\n⚠ CSV download did not work on {name}: {e}")
    else:
        print(f"\n⚠ Clicked '{clicked_text}' but no real application data appeared for {name}.")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon round 6 — structural link-finding + SSL fix\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        for target in TEST_TARGETS:
            await test_council(browser, target["name"], target["url"])
        await browser.close()

    print(f"\n{'=' * 70}")
    print("Round 5 complete.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
