#!/usr/bin/env python3
"""
Hyndburn Northgate platform diagnostic (2026-10-02).

Real question: Hyndburn's URL uses "/Northgate/ES/Presentation/..."
while Broxbourne's (confirmed working) uses "/LPAssure/ES/
Presentation/..." — the same path SUFFIX structure, but "Northgate"
is a genuinely different, real planning software vendor already
referenced elsewhere in this project (Great Yarmouth, South Tyneside,
Merton). This runs the exact same confirmed flow proven for
Broxbourne against Hyndburn's real URL to see how far it actually
gets, rather than assuming either way from the URL alone.
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

URL = "https://planning.hyndburnbc.gov.uk/Northgate/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch"


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
        print("STEP 1: does the same 'Weekly / Monthly' outer toggle exist?")
        print("=" * 60)
        outer_toggle = page.locator("button:has-text('Weekly / Monthly')")
        count = await outer_toggle.count()
        print(f"  Real matches for Broxbourne's exact selector: {count}")

        if count == 0:
            print("\n  ⚠ Does NOT match — dumping every element mentioning "
                  "Weekly/Monthly to find the real equivalent")
            all_candidates = page.locator("text=/Weekly|Monthly/i")
            all_count = await all_candidates.count()
            print(f"  Real elements mentioning Weekly/Monthly: {all_count}")
            for i in range(min(all_count, 15)):
                el = all_candidates.nth(i)
                info = await el.evaluate(
                    "el => ({tag: el.tagName, id: el.id, cls: el.className, "
                    "text: el.textContent.trim().slice(0,60), "
                    "onclick: el.getAttribute('onclick'), "
                    "visible: el.offsetParent !== null})"
                )
                print(f"    [{i}] {info}")

            body_text = await page.locator("body").inner_text()
            print(f"\n  Real body text (first 1500 chars) for manual inspection:")
            print(repr(body_text[:1500]))
        else:
            print("\n  ✓ Matches — continuing with Broxbourne's exact confirmed flow")
            await outer_toggle.click(timeout=5_000)
            await asyncio.sleep(1)

            monthly_link = page.locator("[onclick*='GetOnlinePlanningWeeklySearchView(false)']")
            monthly_count = await monthly_link.count()
            print(f"  Real 'Monthly list' link (Broxbourne's exact onclick) found: {monthly_count}")

            if monthly_count > 0:
                await monthly_link.click(timeout=5_000, force=True)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1.5)

                print("\n" + "=" * 60)
                print("STEP 2: real month dropdown + radio buttons")
                print("=" * 60)
                selects = await page.locator("select").all()
                print(f"  Real <select> elements found: {len(selects)}")
                for i, sel_el in enumerate(selects):
                    name = await sel_el.get_attribute("name")
                    sel_id = await sel_el.get_attribute("id")
                    options = await sel_el.locator("option").all_text_contents()
                    print(f"    [{i}] name={name!r} id={sel_id!r} options={options[:6]}")

                radios = await page.locator("input[type='radio']").all()
                print(f"\n  Real radio inputs found: {len(radios)}")
                for i, radio in enumerate(radios):
                    name = await radio.get_attribute("name")
                    value = await radio.get_attribute("value")
                    radio_id = await radio.get_attribute("id")
                    print(f"    [{i}] name={name!r} value={value!r} id={radio_id!r}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
