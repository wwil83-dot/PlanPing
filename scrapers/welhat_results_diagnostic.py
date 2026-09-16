#!/usr/bin/env python3
"""
PlanFind — Welwyn Hatfield results-page structure diagnostic (2026-09-16).

Real, confirmed context: planning_register_scraper.py's first real
production run confirmed the full search flow works end-to-end for
Welwyn Hatfield (cookie banner dismissed, ISO dates filled, checkbox
set via JS, landing on https://planning.welhat.gov.uk/Search/Results)
but the parser (table.tblResults, same as Worcester/Vale of Glamorgan)
found ZERO rows — 0 saved, despite a real, working search. Given
Worcester and Vale of Glamorgan both had healthy counts in the same
run, this looks like a genuine structural mismatch, not truly empty
data. This reuses the exact confirmed-working search flow, then dumps
every plausible real structural signal on the actual results page.
"""
import asyncio
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

BASE_URL = "https://planning.welhat.gov.uk"
SEARCH_URL = f"{BASE_URL}/Search/Advanced"


async def dismiss_cookie_banner(page) -> None:
    for sel in [
        "#ccc-dismiss-button", "#ccc-notify-accept", "button:has-text('Accept')",
        "button:has-text('I accept')", "button:has-text('OK')",
        "#ccc button", ".ccc-accept-button",
    ]:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click(timeout=3_000)
                print(f"Cookie consent banner dismissed via: {sel!r}")
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue
    print("Could not find a cookie consent dismiss button")


async def dump_real_structure(page, label: str):
    print(f"\n--- Real structure dump: {label} ---")

    title = await page.title()
    print(f"Real page title: {title!r}")
    print(f"Real URL: {page.url}")

    tables = await page.locator("table").all()
    print(f"Real <table> elements found: {len(tables)}")
    for i, t in enumerate(tables[:10]):
        try:
            cls = await t.get_attribute("class")
            id_ = await t.get_attribute("id")
            rows = await t.locator("tr").count()
            print(f"  table[{i}]: class={cls!r} id={id_!r} real row count={rows}")
        except Exception:
            continue

    result_like = await page.locator(
        "[class*='result' i], [id*='result' i]"
    ).all()
    print(f"Elements with 'result' in class/id: {len(result_like)}")
    for i, el in enumerate(result_like[:10]):
        try:
            tag = await el.evaluate("el => el.tagName")
            cls = await el.get_attribute("class")
            id_ = await el.get_attribute("id")
            print(f"  [{i}] <{tag}> class={cls!r} id={id_!r}")
        except Exception:
            continue

    body_text = (await page.locator("body").inner_text())[:500]
    print(f"Real body text (first 500 chars): {body_text!r}")


async def main():
    today = date.today()
    start = today - timedelta(days=30)
    start_str = start.isoformat()
    end_str = today.isoformat()

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

        await dismiss_cookie_banner(page)

        await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
        await page.fill("#DateReceivedTo", end_str, timeout=5_000)
        print(f"Filled real date range (ISO): {start_str} to {end_str}")

        planning_checkbox = page.locator("input[name='SearchPlanning'][type='checkbox']")
        if await planning_checkbox.count() > 0 and not await planning_checkbox.first.is_checked():
            await planning_checkbox.first.evaluate(
                "el => { el.checked = true; el.dispatchEvent(new Event('change', {bubbles: true})); }"
            )
            print("SearchPlanning checkbox set via direct JS")

        form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
        scope = form_loc if await form_loc.count() > 0 else page
        submit = scope.locator(
            "button:has-text('Search'):visible, input[type='submit'][value*='Search' i]:visible"
        )
        submit_count = await submit.count()
        print(f"Real visible Search submit count: {submit_count}")

        if submit_count > 0:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
                await submit.first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            await dump_real_structure(page, "immediately after submit")

            html = await page.content()
            with open("/tmp/welhat_results_diag.html", "w", encoding="utf-8") as f:
                f.write(html)
            await page.screenshot(path="/tmp/welhat_results_diag.png", full_page=True)
            print("\nSaved real HTML and screenshot for inspection")
        else:
            print("Could not find a real submit button — stopping")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
