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
        print("STEP 1: find the real 'Weekly/Monthly' control")
        print("=" * 60)
        weekly_monthly_candidates = [
            "a:has-text('Weekly')", "button:has-text('Weekly')",
            "a:has-text('Monthly')", "button:has-text('Monthly')",
            "*:has-text('Weekly/Monthly')",
        ]
        found_control = None
        for sel in weekly_monthly_candidates:
            loc = page.locator(sel)
            count = await loc.count()
            if count > 0:
                print(f"  candidate {sel!r}: {count} match(es)")
                if found_control is None:
                    found_control = loc.first

        if found_control is None:
            print("  ⚠ No real Weekly/Monthly control found by any candidate selector")
            body_text = await page.locator("body").inner_text()
            print(f"\n  Real body text (first 2000 chars) for manual inspection:")
            print(repr(body_text[:2000]))
        else:
            tag_info = await found_control.evaluate(
                "el => ({tag: el.tagName, id: el.id, cls: el.className, text: el.textContent.trim()})"
            )
            print(f"\n  Real control found: {tag_info}")
            await found_control.click(timeout=5_000)
            await asyncio.sleep(1)

            print("\n" + "=" * 60)
            print("STEP 2: real box/panel that opened")
            print("=" * 60)
            body_text = await page.locator("body").inner_text()
            print(f"  Real body text after click (first 2000 chars):")
            print(repr(body_text[:2000]))

            monthly_option = page.locator("a:has-text('Monthly'), button:has-text('Monthly'), label:has-text('Monthly')")
            count = await monthly_option.count()
            print(f"\n  Real 'Monthly' option candidates found: {count}")
            if count > 0:
                await monthly_option.first.click(timeout=5_000)
                await asyncio.sleep(1)

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

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
