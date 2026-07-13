#!/usr/bin/env python3
"""
PlanFind — Arcus reconnaissance script, round 7: structural link-finding.

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
        "name": "Ashford Borough Council",
        "url": "https://ashfordboroughcouncil.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register",
    },
    {
        "name": "Epping Forest District Council",
        "url": "https://eppingforestdc.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register",
    },
    {
        "name": "Manchester City Council",
        "url": "https://arcusbe.manchester.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register",
    },
]

# HYPOTHESIS BEING TESTED: rounds 4-7 confirmed each council words its
# "weekly list" quick-links completely differently (Ashford: "Planning
# Applications Weekly List". Epping Forest: "...Validated this week" /
# "...Decided this week". Manchester: "Decision List by Date (7 days)" /
# "Weekly List by Date (7 days)") — no single magic string works
# everywhere. BUT "Advanced search" appears identically worded on every
# council's homepage seen so far (Ashford, Manchester, and visible in
# Epping Forest's own screenshot too), with a rich set of real date-picker
# fields. If THIS is consistent, it's the true portable mechanism — the
# Arcus equivalent of Idox's monthYearIndex URL parameter.

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
    print(f"Contains 'Advanced search' link: {'Advanced search' in homepage_html}")

    with open(f"/tmp/arcus_r9_{name.replace(' ', '_')}_00_homepage.html", "w", encoding="utf-8") as f:
        f.write(homepage_html)
    await page.screenshot(path=f"/tmp/arcus_r9_{name.replace(' ', '_')}_00_homepage.png", full_page=True)

    # --- ADVANCED SEARCH approach: "Advanced search" appears identically
    # worded on every council's homepage seen so far, unlike the
    # inconsistently-worded weekly-list quick-links. Testing whether this
    # is the truly portable mechanism.
    clicked = False
    try:
        loc = page.get_by_text("Advanced search", exact=False)
        count = await loc.count()
        print(f"  Checking for 'Advanced search': found {count}")
        if count > 0:
            await loc.first.click(timeout=5_000)
            clicked = True
            print("\n✓ Clicked 'Advanced search'")
    except Exception as e:
        print(f"  ⚠ Error clicking 'Advanced search': {e}")

    if not clicked:
        print("\n⚠ 'Advanced search' link not found on this page.")
        await context.close()
        return

    await asyncio.sleep(5)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    form_html = await page.content()
    with open(f"/tmp/arcus_r9_{name.replace(' ', '_')}_01_advanced_form.html", "w", encoding="utf-8") as f:
        f.write(form_html)
    await page.screenshot(path=f"/tmp/arcus_r9_{name.replace(' ', '_')}_01_advanced_form.png", full_page=True)

    # --- FIX (round 9): a real user screenshot of Manchester's Advanced
    # Search showed the Category dropdown DEFAULTS to "Building Control
    # Applications", not "Planning Applications" — our date-only search in
    # round 9 likely ran against the wrong category entirely, which alone
    # would explain zero results across ALL THREE councils regardless of
    # whether the date field filled correctly. Explicitly select the
    # planning category before doing anything else.
    category_selected = False
    for category_variant in ["Planning Applications", "Planning Application", "Planning"]:
        try:
            category_dropdown = page.get_by_label("Category", exact=False)
            if await category_dropdown.count() > 0:
                await category_dropdown.first.select_option(label=category_variant, timeout=5_000)
                print(f"✓ Selected Category: '{category_variant}'")
                category_selected = True
                break
        except Exception:
            continue

    if not category_selected:
        print("⚠ Could not select a 'Planning Applications' category — may already default "
              "correctly, or the dropdown uses a different mechanism (Lightning combobox "
              "rather than a native <select>).")

    # --- Try filling in a 14-day date range, matching the Idox scraper's
    # own DAYS_BACK=14 default. Fill BOTH from/to this time (round 9 only
    # filled "from"), and press Tab after each fill to force the Lightning
    # component to properly commit the value to its internal state — a
    # plain .fill() can sometimes leave a controlled input's underlying
    # framework state unchanged even though the visible text updates.
    from datetime import date, timedelta
    today = date.today()
    two_weeks_ago = today - timedelta(days=14)
    date_from_str = two_weeks_ago.strftime("%d/%m/%Y")
    date_to_str = today.strftime("%d/%m/%Y")

    filled_from = False
    for label_variant in ["Valid date from", "Date from", "Received date from"]:
        try:
            date_input = page.get_by_label(label_variant, exact=False)
            if await date_input.count() > 0:
                await date_input.first.fill(date_from_str, timeout=5_000)
                await date_input.first.press("Tab")
                print(f"✓ Filled '{label_variant}' with {date_from_str} (+ Tab to commit)")
                filled_from = True
                break
        except Exception:
            continue

    filled_to = False
    for label_variant in ["Valid date to", "Date to", "Received date to"]:
        try:
            date_input = page.get_by_label(label_variant, exact=False)
            if await date_input.count() > 0:
                await date_input.first.fill(date_to_str, timeout=5_000)
                await date_input.first.press("Tab")
                print(f"✓ Filled '{label_variant}' with {date_to_str} (+ Tab to commit)")
                filled_to = True
                break
        except Exception:
            continue

    if not filled_from:
        print("⚠ Could not find a 'date from' field to fill using known label variants.")
    if not filled_to:
        print("⚠ Could not find a 'date to' field to fill using known label variants.")

    await asyncio.sleep(1)

    try:
        search_button = page.get_by_role("button", name="Search", exact=False)
        if await search_button.count() > 0:
            await search_button.first.click(timeout=5_000)
            print("✓ Clicked Search button")
        else:
            print("⚠ Could not find a Search button")
    except Exception as e:
        print(f"⚠ Error clicking Search: {e}")

    await asyncio.sleep(5)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    results_html = await page.content()
    print(f"\nPage title after search: '{await page.title()}'")
    print(f"HTML length after search: {len(results_html)} chars")

    indicators = {
        "Application Reference": results_html.count("Application Reference"),
        "Reference": results_html.count("Reference"),
        "Site Address": results_html.count("Site Address") + results_html.count("Site address"),
        "Download as CSV": results_html.count("Download as CSV"),
        "Showing": results_html.count("Showing"),
    }
    for k, v in indicators.items():
        print(f"  Count of '{k}': {v}")

    with open(f"/tmp/arcus_r9_{name.replace(' ', '_')}_02_search_results.html", "w", encoding="utf-8") as f:
        f.write(results_html)
    await page.screenshot(path=f"/tmp/arcus_r9_{name.replace(' ', '_')}_02_search_results.png", full_page=True)

    if indicators["Application Reference"] > 0 or indicators["Reference"] > 3 or indicators["Download as CSV"] > 0:
        print(f"\n✓✓✓ Advanced Search approach appears to have worked for {name}!")
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await page.get_by_text("Download as CSV", exact=False).first.click()
            download = await download_info.value
            csv_path = f"/tmp/arcus_r9_{name.replace(' ', '_')}.csv"
            await download.save_as(csv_path)
            with open(csv_path, encoding="utf-8", errors="replace") as f:
                preview = f.read(600)
            print(f"✓✓✓ CSV download also succeeded!")
            print(f"--- CSV preview (first 600 chars) ---\n{preview}")
        except Exception as e:
            print(f"⚠ CSV download did not work (but search results did render): {e}")
    else:
        print(f"\n⚠ No real application data appeared for {name} via Advanced Search.")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon round 9 — Advanced Search portability test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        for target in TEST_TARGETS:
            await test_council(browser, target["name"], target["url"])
        await browser.close()

    print(f"\n{'=' * 70}")
    print("Round 9 complete.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
