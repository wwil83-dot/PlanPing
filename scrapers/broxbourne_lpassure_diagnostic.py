#!/usr/bin/env python3
"""
Broxbourne LPAssure platform diagnostic (2026-09-30).

Real, direct description from the person running this project: a
"Weekly/Monthly" button on the left side opens a box with Weekly/
Monthly options; picking Monthly reveals a month dropdown near the top
right, plus Decided/Validated radio buttons below it (one search type
at a time, not both together).

This confirms the real HTML structure behind that description —
button/box selectors, the real dropdown and radio names/values, and
the real results page structure after a submission — before writing
any scraper code, since LPAssure hasn't been used anywhere else in
this project and nothing about its real markup is known yet.
"""
import asyncio

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

URL = "https://planning.broxbourne.gov.uk/LPAssure/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"Real page title: {await page.title()}")
        print(f"Real page URL after load: {page.url}\n")

        for sel in ["button:has-text('Accept')", "button:has-text('I agree')",
                    "button:has-text('OK')"]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                print(f"Real cookie-style banner dismissed via: {sel!r}")
                await loc.first.click(timeout=3_000)
                break

        print("=" * 60)
        print("STEP 0: dump every real element mentioning 'Weekly' or 'Monthly',")
        print("        visible or not, so the real OUTER toggle can be told")
        print("        apart from the inner (initially hidden) list links")
        print("=" * 60)
        all_candidates = page.locator("text=/Weekly|Monthly/i")
        all_count = await all_candidates.count()
        print(f"  Real elements mentioning Weekly/Monthly: {all_count}")
        for i in range(all_count):
            el = all_candidates.nth(i)
            info = await el.evaluate(
                "el => ({tag: el.tagName, id: el.id, cls: el.className, "
                "text: el.textContent.trim().slice(0,60), "
                "onclick: el.getAttribute('onclick'), "
                "visible: el.offsetParent !== null})"
            )
            print(f"    [{i}] {info}")

        print("\n" + "=" * 60)
        print("STEP 1: find the real OUTER 'Weekly/Monthly' toggle "
              "(left-hand side, per direct description)")
        print("=" * 60)
        # Real, more targeted candidates — looking specifically for a
        # combined "Weekly/Monthly" label (the outer toggle, per the
        # direct description), or a real, visible parent control,
        # rather than matching "Weekly" or "Monthly" alone, which
        # found the inner (hidden) list links instead last time.
        toggle_candidates = [
            "a:has-text('Weekly/Monthly')", "button:has-text('Weekly/Monthly')",
            "a:has-text('Weekly / Monthly')", "button:has-text('Weekly / Monthly')",
            "[onclick*='WeeklyMonthly' i]:visible",
        ]
        found_control = None
        for sel in toggle_candidates:
            loc = page.locator(sel)
            count = await loc.count()
            if count > 0:
                print(f"  candidate {sel!r}: {count} match(es)")
                if found_control is None:
                    found_control = loc.first

        if found_control is None:
            print("  ⚠ No real outer toggle found by any candidate selector — "
                  "see STEP 0's full dump above to identify it manually")
        else:
            tag_info = await found_control.evaluate(
                "el => ({tag: el.tagName, id: el.id, cls: el.className, text: el.textContent.trim()})"
            )
            print(f"\n  Real outer toggle found: {tag_info}")
            await found_control.click(timeout=5_000)
            await asyncio.sleep(1)

            print("\n" + "=" * 60)
            print("STEP 2: real box/panel that opened")
            print("=" * 60)
            body_text = await page.locator("body").inner_text()
            print(f"  Real body text after click (first 2000 chars):")
            print(repr(body_text[:2000]))

            # REAL FIX — confirmed via the actual run: a broad text
            # match on "Monthly" accidentally matched the OUTER
            # "Weekly / Monthly list" toggle button again (it also
            # contains the word "Monthly"), likely re-closing the box
            # rather than opening the month-specific view — explaining
            # why Step 3 found the general search form's radios instead
            # of a month dropdown. Targeting the confirmed, precise
            # onclick handler instead, which can only match the real
            # inner "Monthly list" link.
            monthly_option = page.locator(
                "[onclick*='GetOnlinePlanningWeeklySearchView(false)']"
            )
            count = await monthly_option.count()
            print(f"\n  Real 'Monthly list' link (precise onclick match) found: {count}")
            if count > 0:
                await monthly_option.first.click(timeout=5_000, force=True)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(2)

                print("\n" + "=" * 60)
                print("STEP 3: real month dropdown + radio buttons")
                print("=" * 60)
                selects = await page.locator("select").all()
                print(f"  Real <select> elements found: {len(selects)}")
                for i, sel_el in enumerate(selects):
                    name = await sel_el.get_attribute("name")
                    sel_id = await sel_el.get_attribute("id")
                    options = await sel_el.locator("option").all_text_contents()
                    print(f"    [{i}] name={name!r} id={sel_id!r} options={options[:10]}")

                radios = await page.locator("input[type='radio']").all()
                print(f"\n  Real radio inputs found: {len(radios)}")
                for i, radio in enumerate(radios):
                    name = await radio.get_attribute("name")
                    value = await radio.get_attribute("value")
                    radio_id = await radio.get_attribute("id")
                    label_text = ""
                    if radio_id:
                        label = page.locator(f"label[for='{radio_id}']")
                        if await label.count() > 0:
                            label_text = await label.first.text_content()
                    print(f"    [{i}] name={name!r} value={value!r} id={radio_id!r} label={label_text.strip()!r}")

                submit_candidates = await page.locator(
                    "input[type='submit'], button[type='submit'], button:has-text('Search')"
                ).all()
                print(f"\n  Real submit-style controls found: {len(submit_candidates)}")
                for i, s in enumerate(submit_candidates):
                    text = (await s.text_content() or "").strip()
                    tag = await s.evaluate("el => el.tagName")
                    print(f"    [{i}] tag={tag} text={text!r}")

                print("\n" + "=" * 60)
                print("STEP 4: real search — September 2026, Validated this month")
                print("=" * 60)
                month_select = page.locator("select#SelectedMonth")
                await month_select.select_option(label="September 2026", timeout=5_000)
                print("  Real month selected: September 2026")

                validated_radio = page.locator("input#ValidatedThisMonth")
                await validated_radio.check(timeout=5_000, force=True)
                print("  Real 'Validated this month' radio checked")

                # REAL FIX — confirmed via the actual run: the previous
                # .first match against any "Search"-text button grabbed
                # the wrong one (the general search form's own button),
                # not the monthly-list panel's dedicated Search/Cancel
                # pair confirmed in the real body text ("Search Cancel"
                # at the very end, right after the ward/status fields).
                # Dumping every EXACT "Search" button's real context
                # directly, rather than guessing which one is right a
                # second time.
                exact_search_buttons = page.locator("button", has_text="Search")
                count = await exact_search_buttons.count()
                print(f"  Real buttons containing 'Search' text: {count}")
                target_btn = None
                for i in range(count):
                    btn = exact_search_buttons.nth(i)
                    text = (await btn.text_content() or "").strip()
                    visible = await btn.is_visible()
                    parent_id = await btn.evaluate(
                        "el => el.closest('[id]') ? el.closest('[id]').id : null"
                    )
                    print(f"    [{i}] text={text!r} visible={visible} nearest_id_ancestor={parent_id!r}")
                    if text == "Search" and visible and target_btn is None:
                        target_btn = btn

                if target_btn is None:
                    print("  ⚠ No real exact-match, visible 'Search' button found — "
                          "cannot submit, see the dump above to identify the right one")
                    await context.close()
                    await browser.close()
                    return

                search_btn = target_btn
                await search_btn.click(timeout=5_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(2)

                print(f"\n  Real results URL: {page.url}")
                print(f"  Real results page title: {await page.title()}")

                body_text = await page.locator("body").inner_text()
                print(f"\n  Real results body text (first 2500 chars):")
                print(repr(body_text[:2500]))

                tables = page.locator("table")
                table_count = await tables.count()
                print(f"\n  Real <table> elements found: {table_count}")

                # Real, direct identification of the actual results
                # table among the 4 found — the one containing the
                # confirmed real header text "Reference No." — rather
                # than guessing which index it is.
                for i in range(table_count):
                    t = tables.nth(i)
                    t_text = await t.inner_text()
                    if "Reference No" in t_text:
                        print(f"\n  Real results table identified at index {i}")
                        row_count = await t.locator("tr").count()
                        print(f"  Real <tr> rows in this table: {row_count}")
                        html = await t.evaluate("el => el.outerHTML")
                        print(f"\n  Real, exact HTML of this table (first 4000 chars):")
                        print(html[:4000])
                        break
                else:
                    print(f"  ⚠ None of the {table_count} real tables contained "
                          f"'Reference No' — results may be in a different structure")

                print("\n" + "=" * 60)
                print("STEP 5: real second search — same month, Decided this month")
                print("=" * 60)
                # Real, confirmed from the original description: these
                # two radios are mutually exclusive (same name attribute,
                # MonthlyListStatus) — checking Decided should
                # automatically uncheck Validated. Reusing the same
                # confirmed-correct Search button location from Step 4.
                decided_radio = page.locator("input#DecidedThisMonth")
                await decided_radio.check(timeout=5_000, force=True)
                print("  Real 'Decided this month' radio checked")

                # Re-finding by the same confirmed ancestor id from
                # Step 4, rather than reusing a stale element reference.
                all_search_btns = page.locator("button", has_text="Search")
                target_btn_2 = None
                for i in range(await all_search_btns.count()):
                    btn = all_search_btns.nth(i)
                    text = (await btn.text_content() or "").strip()
                    parent_id = await btn.evaluate(
                        "el => el.closest('[id]') ? el.closest('[id]').id : null"
                    )
                    if text == "Search" and parent_id == "ancWeeklyMonthlySearch":
                        target_btn_2 = btn
                        break

                if target_btn_2 is None:
                    print("  ⚠ Could not re-find the confirmed Search button for this second search")
                else:
                    await target_btn_2.click(timeout=5_000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                    await asyncio.sleep(2)

                    print(f"\n  Real results URL: {page.url}")
                    body_text_2 = await page.locator("body").inner_text()

                    import re as _re
                    count_match = _re.search(r"(\d+)\s+Results", body_text_2)
                    print(f"  Real result count found: {count_match.group(1) if count_match else 'not found'}")

                    tables_2 = page.locator("table")
                    for i in range(await tables_2.count()):
                        t = tables_2.nth(i)
                        t_text = await t.inner_text()
                        if "Reference No" in t_text:
                            print(f"\n  Real Decided-this-month results (first 1500 chars of body):")
                            print(repr(body_text_2[body_text_2.find("Reference No"):body_text_2.find("Reference No") + 1500]))
                            break
                    else:
                        print(f"  ⚠ No real results table with 'Reference No' found for Decided this month")

                # Real, common result-list containers to check for,
                # since LPAssure's own markup isn't yet confirmed —
                # trying several plausible candidates rather than
                # assuming one.
                list_candidates = [
                    "ul.search-results", "div.search-results",
                    "ul.results-list", "div.results-list",
                    "[class*='result' i]",
                ]
                for sel in list_candidates:
                    loc = page.locator(sel)
                    count = await loc.count()
                    if count > 0:
                        print(f"\n  Real candidate result container {sel!r}: {count} match(es)")
                        html = await loc.first.evaluate("el => el.outerHTML")
                        print(f"  First match's real HTML (first 2000 chars):")
                        print(html[:2000])
                        break

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
