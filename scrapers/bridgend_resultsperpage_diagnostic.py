#!/usr/bin/env python3
"""
PlanFind — Bridgend resultsPerPage=50 shortcut diagnostic (2026-09-11).

Real question, direct follow-up to bridgend_results_diagnostic.py:
that diagnostic confirmed a real, visible <select id="resultsPerPage">
with options 10/25/50 on Bridgend's real results page — before
building numbered-pagination click logic (a genuinely different UI
pattern from every other confirmed platform so far), this tests
whether simply selecting "50" reliably shows more rows in one page,
which would avoid needing precise pagination selectors at all for most
realistic day-to-day volumes.

HONEST CONTEXT: Telford had a very similar-looking page-size dropdown
that did NOT work reliably (the server-side page size silently reset
on tab switch regardless of what the dropdown displayed) — this is
tested directly rather than assumed to behave the same or differently.
"""
import asyncio
import re
from datetime import date, timedelta

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

SEARCH_URL = "https://planning.bridgend.gov.uk/Search/Planning/Advanced"


async def count_real_rows(page) -> int:
    """Real row count in the confirmed results table, minus 1 for the
    header row — matches the exact table.table selector confirmed by
    the prior diagnostic."""
    table = page.locator("table.table").first
    if await table.count() == 0:
        return 0
    rows = await table.locator("tr").count()
    return max(0, rows - 1)


async def main():
    today = date.today()
    start = today - timedelta(days=30)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        if "Disclaimer" in page.url:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                await page.click("input[value='Agree']", timeout=5_000)

        await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
        await page.fill("#DateReceivedTo", end_str, timeout=5_000)

        form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
        submit = form_loc.locator("input[type='submit']:visible")
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit.first.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"Real post-submit URL: {page.url}")

        before_count = await count_real_rows(page)
        print(f"\nReal row count BEFORE changing resultsPerPage: {before_count}")

        dropdown = page.locator("#resultsPerPage")
        dd_count = await dropdown.count()
        print(f"Real #resultsPerPage dropdown matches: {dd_count}")
        if dd_count == 0:
            print("Dropdown not found — cannot test the shortcut.")
            await context.close()
            await browser.close()
            return

        current_value = await dropdown.input_value()
        print(f"Real dropdown value before selection: {current_value!r}")

        # Try selecting "50" and see what actually happens — a real
        # navigation, an in-place DOM update, or nothing at all.
        navigated = False
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=8_000):
                await dropdown.select_option(value="50", timeout=5_000)
            navigated = True
            print("Real navigation occurred after selecting 50 (select auto-submits on change)")
        except PlaywrightTimeout:
            print("No real navigation occurred within 8s after selecting 50 — "
                  "checking for an in-place update instead")

        # Whether or not a navigation fired, wait briefly and re-check —
        # an in-place AJAX update wouldn't trigger Playwright's
        # navigation event at all, but could still update the DOM.
        await asyncio.sleep(3)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        after_value = await dropdown.input_value()
        after_count = await count_real_rows(page)
        print(f"\nReal dropdown value after selection: {after_value!r}")
        print(f"Real row count AFTER selecting 50: {after_count}")

        if after_count > before_count:
            print(f"\nREAL CONFIRMATION: row count increased ({before_count} -> "
                  f"{after_count}) — the resultsPerPage=50 shortcut WORKS.")
        elif after_value == "50" and after_count == before_count:
            print(f"\nREAL FINDING: dropdown shows '50' selected but row count "
                  f"DIDN'T increase ({before_count} -> {after_count}) — same "
                  f"category of issue as Telford's page-size dropdown. The "
                  f"displayed value doesn't reflect the real active page size.")
        else:
            print(f"\nREAL FINDING: selection didn't take effect at all "
                  f"(dropdown still shows {after_value!r}). Numbered pagination "
                  f"is the way forward instead.")

        # Real evidence either way — save current state for inspection.
        html = await page.content()
        with open("/tmp/bridgend_resultsperpage_test.html", "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path="/tmp/bridgend_resultsperpage_test.png", full_page=True)

        # Also capture the real pagination control's markup directly,
        # regardless of the shortcut's outcome — needed either way if
        # numbered-page clicking turns out to be necessary.
        print("\n--- Real pagination control markup ---")
        pagination_containers = await page.locator(
            "[class*='pag' i], nav, ul.pagination"
        ).all()
        for i, el in enumerate(pagination_containers[:5]):
            try:
                outer = await el.evaluate("el => el.outerHTML")
                print(f"  [{i}] {outer[:500]!r}")
            except Exception:
                continue

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
