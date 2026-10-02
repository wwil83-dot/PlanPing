#!/usr/bin/env python3
"""
Broxbourne LPAssure pagination diagnostic (2026-10-02).

Real, confirmed problem: a production run showed EVERY month/status
search returning exactly 20 applications across 6 months — but the
original diagnostic confirmed September 2026 alone had 79 Validated
and 97 Decided real results. The scraper's "a:has-text('Next'):visible"
selector never found a match, so pagination silently stopped after
page 1 every single time. This finds the real pagination control
directly rather than guessing at a second selector blind.
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

SEARCH_URL = "https://planning.broxbourne.gov.uk/LPAssure/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        outer_toggle = page.locator("button:has-text('Weekly / Monthly')")
        await outer_toggle.click(timeout=5_000)
        await asyncio.sleep(1)

        monthly_link = page.locator("[onclick*='GetOnlinePlanningWeeklySearchView(false)']")
        await monthly_link.click(timeout=5_000, force=True)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1.5)

        month_select = page.locator("select#SelectedMonth")
        await month_select.select_option(label="September 2026", timeout=5_000)

        validated_radio = page.locator("input#ValidatedThisMonth")
        await validated_radio.check(timeout=5_000, force=True)

        all_search_btns = page.locator("button", has_text="Search")
        target_btn = None
        for i in range(await all_search_btns.count()):
            btn = all_search_btns.nth(i)
            text = (await btn.text_content() or "").strip()
            parent_id = await btn.evaluate(
                "el => el.closest('[id]') ? el.closest('[id]').id : null"
            )
            if text == "Search" and parent_id == "ancWeeklyMonthlySearch":
                target_btn = btn
                break

        await target_btn.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(2)

        body_text = await page.locator("body").inner_text()
        import re
        count_match = re.search(r"(\d+)\s+Results", body_text)
        print(f"Real confirmed result count: {count_match.group(1) if count_match else 'not found'}")

        print("\n" + "=" * 60)
        print("Real pagination-area elements, dumped directly")
        print("=" * 60)

        candidates = page.locator("a, button, li, span").filter(has_text="2")
        total_candidates = await candidates.count()
        print(f"Real elements containing the text '2' (potential page-2 link): {total_candidates}")
        for i in range(min(total_candidates, 15)):
            el = candidates.nth(i)
            info = await el.evaluate(
                "el => ({tag: el.tagName, id: el.id, cls: el.className, "
                "text: el.textContent.trim().slice(0,40), "
                "onclick: el.getAttribute('onclick'), "
                "href: el.getAttribute('href'), "
                "visible: el.offsetParent !== null})"
            )
            print(f"  [{i}] {info}")

        print("\n" + "=" * 60)
        print("Real elements with 'page' in id/class, case-insensitive")
        print("=" * 60)
        page_like = page.locator("[id*='page' i], [class*='page' i], [id*='pag' i], [class*='pag' i]")
        count = await page_like.count()
        print(f"Found: {count}")
        for i in range(min(count, 15)):
            el = page_like.nth(i)
            info = await el.evaluate(
                "el => ({tag: el.tagName, id: el.id, cls: el.className, "
                "text: el.textContent.trim().slice(0,60), "
                "onclick: el.getAttribute('onclick'), "
                "visible: el.offsetParent !== null})"
            )
            print(f"  [{i}] {info}")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
