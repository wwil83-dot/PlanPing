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

    with open(f"/tmp/arcus_r12_{name.replace(' ', '_')}_00_homepage.html", "w", encoding="utf-8") as f:
        f.write(homepage_html)
    await page.screenshot(path=f"/tmp/arcus_r12_{name.replace(' ', '_')}_00_homepage.png", full_page=True)

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
    with open(f"/tmp/arcus_r12_{name.replace(' ', '_')}_01_advanced_form.html", "w", encoding="utf-8") as f:
        f.write(form_html)
    await page.screenshot(path=f"/tmp/arcus_r12_{name.replace(' ', '_')}_01_advanced_form.png", full_page=True)

    # --- FIX (round 12): round 12's select_option() failed on all three
    # councils — confirming this is a Lightning combobox component, not a
    # native <select>. Lightning comboboxes use a standard pattern: click
    # the trigger to open a listbox (role="listbox" with role="option"
    # items), then click the desired option text. Also save a screenshot
    # right after opening it, so if this still fails we have direct visual
    # ground truth of what actually appeared instead of guessing further.
    category_selected = False
    try:
        category_field = page.get_by_label("Category", exact=False)
        if await category_field.count() > 0:
            await category_field.first.click(timeout=5_000)
            await asyncio.sleep(1)
            await page.screenshot(
                path=f"/tmp/arcus_r12_{name.replace(' ', '_')}_category_open.png",
                full_page=True,
            )
            for category_variant in ["Planning Applications", "Planning Application", "Planning"]:
                option = page.get_by_role("option", name=category_variant, exact=False)
                if await option.count() > 0:
                    await option.first.click(timeout=5_000)
                    print(f"✓ Selected Category via Lightning combobox: '{category_variant}'")
                    category_selected = True
                    break
            if not category_selected:
                # Print whatever options DID appear, so we have ground
                # truth instead of another blind guess next round.
                all_options = page.get_by_role("option")
                opt_count = await all_options.count()
                print(f"  Category combobox opened but no planning-related option matched. "
                      f"Found {opt_count} option(s):")
                for i in range(min(opt_count, 15)):
                    try:
                        opt_text = await all_options.nth(i).inner_text()
                        print(f"    Option {i}: '{opt_text.strip()}'")
                    except Exception:
                        pass
        else:
            print("⚠ Could not find a 'Category' labelled field at all.")
    except Exception as e:
        print(f"⚠ Error interacting with Category combobox: {e}")

    if not category_selected:
        print("⚠ Category selection did not succeed — search below may still run "
              "against the wrong default category.")

    # --- FIX (round 12): round 12 showed Manchester's date fields, which
    # worked fine in round 9 (before category selection was added), could
    # no longer be found once we select the category FIRST. Strong signal
    # that selecting the category triggers a dynamic form re-render (a
    # different application type likely shows a different field set), and
    # our date-field search was running too early — against DOM elements
    # mid-swap. Give it a proper wait before continuing.
    if category_selected:
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(3)
        print("  (waited for form to settle after category selection)")

    # --- Try filling in a 14-day date range, matching the Idox scraper's
    # own DAYS_BACK=14 default. Fill BOTH from/to this time (round 12 only
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

    # --- VERIFY, don't just trust: read back whatever value is actually
    # sitting in the date fields right now, since a plain .fill() can
    # visually update an input without the underlying Lightning component
    # framework ever registering the change internally. This is the ground
    # truth check that tells us whether "✓ Filled" above was a real success
    # or just a cosmetic one.
    if filled_from or filled_to:
        for label_variant in ["Valid date from", "Date from", "Received date from",
                               "Valid date to", "Date to", "Received date to"]:
            try:
                field = page.get_by_label(label_variant, exact=False)
                if await field.count() > 0:
                    actual_value = await field.first.input_value()
                    print(f"  Verified actual value in '{label_variant}': '{actual_value}'")
            except Exception:
                continue

    await asyncio.sleep(1)

    # Screenshot the EXACT state of the form right before clicking Search —
    # the single most important piece of ground truth if results still
    # come back empty after this.
    await page.screenshot(
        path=f"/tmp/arcus_r12_{name.replace(' ', '_')}_02_form_before_search.png",
        full_page=True,
    )
    with open(f"/tmp/arcus_r12_{name.replace(' ', '_')}_02_form_before_search.html", "w", encoding="utf-8") as f:
        f.write(await page.content())

    try:
        search_button = page.get_by_role("button", name="Search", exact=False)
        btn_count = await search_button.count()
        print(f"  Total 'Search' buttons found on page: {btn_count}")

        if btn_count == 0:
            print("⚠ Could not find any Search button")
        elif btn_count == 1:
            await search_button.first.click(timeout=5_000)
            print("✓ Clicked the only Search button found")
        else:
            # HYPOTHESIS (round 12): Ashford's form was verified correctly
            # filled (category + both dates, read back via input_value())
            # yet still returned zero results in round 12. If there's more
            # than one "Search" button on the page — e.g. a quick-search
            # button near the top search box, separate from the Advanced
            # Search form's own submit button — .first may have clicked
            # the WRONG one, meaning the carefully-filled form was never
            # actually submitted at all. Form submit buttons typically sit
            # at the bottom of the page, after all fields, so try .last
            # instead of .first.
            for i in range(btn_count):
                try:
                    box = await search_button.nth(i).bounding_box()
                    print(f"    Search button {i}: position={box}")
                except Exception:
                    print(f"    Search button {i}: (could not get position)")

            await search_button.last.click(timeout=5_000)
            print(f"✓ Clicked the LAST of {btn_count} Search buttons found "
                  f"(hypothesis: form-submit button, not quick-search)")
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

    with open(f"/tmp/arcus_r12_{name.replace(' ', '_')}_02_search_results.html", "w", encoding="utf-8") as f:
        f.write(results_html)
    await page.screenshot(path=f"/tmp/arcus_r12_{name.replace(' ', '_')}_02_search_results.png", full_page=True)

    if indicators["Application Reference"] > 0 or indicators["Reference"] > 3 or indicators["Download as CSV"] > 0:
        print(f"\n✓✓✓ Advanced Search approach appears to have worked for {name}!")
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await page.get_by_text("Download as CSV", exact=False).first.click()
            download = await download_info.value
            csv_path = f"/tmp/arcus_r12_{name.replace(' ', '_')}.csv"
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon round 12 — Advanced Search portability test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        for target in TEST_TARGETS:
            await test_council(browser, target["name"], target["url"])
        await browser.close()

    print(f"\n{'=' * 70}")
    print("Round 12 complete.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
