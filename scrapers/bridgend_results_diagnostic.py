#!/usr/bin/env python3
"""
PlanFind — Bridgend results-page structure diagnostic (2026-09-11).

Real, confirmed context: fylde_cluster_recon.py established that
Bridgend shares Fylde's exact search-FORM platform (same field names,
same SearchPlanning checkbox requirement, same disclaimer pattern) and
a real search submission genuinely succeeds, landing on
https://planning.bridgend.gov.uk/Search/Results — but that results
page scored 0/4 on every Fylde-specific fingerprint (no tblResults
table, no "Next Page." aria-label). The recon never captured WHAT the
real results page actually contains — only that it doesn't match
Fylde's pattern. This fills that gap.

Real, confirmed working search flow, reused directly from the recon:
  - Disclaimer click via input[value='Agree']
  - Fill #DateReceivedFrom / #DateReceivedTo (DD/MM/YYYY)
  - Submit via input[type='submit'] scoped to the form (id='searchButton')

This does NOT assume the results are simple static HTML — Bridgend's
search form had several hidden fields (Module, SortDirection) not seen
on Fylde/Worcester/Vale of Glamorgan's own forms, suggesting a
genuinely different underlying results-rendering approach is plausible
(e.g. results loaded/re-rendered via a separate JS call after the
initial page load). Waits generously and checks the DOM at multiple
points rather than assuming one snapshot immediately after navigation
is definitive.
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

SEARCH_URL = "https://planning.bridgend.gov.uk/Search/Planning/Advanced"


async def dump_real_structure(page, label: str):
    """Prints every plausible real structural signal found on the page
    right now — not just Fylde's specific tblResults/Next-Page pattern,
    since Bridgend's platform is now confirmed different. Casts a wide
    net rather than guessing at one likely selector."""
    print(f"\n    --- Real structure dump: {label} ---")

    # Every real table on the page, with its class/id and row count —
    # whatever Bridgend's actual results table is called, it'll show
    # up here regardless of what we guess to call it.
    tables = await page.locator("table").all()
    print(f"    Real <table> elements found: {len(tables)}")
    for i, t in enumerate(tables):
        try:
            cls = await t.get_attribute("class")
            id_ = await t.get_attribute("id")
            rows = await t.locator("tr").count()
            print(f"      table[{i}]: class={cls!r} id={id_!r} real row count={rows}")
        except Exception:
            continue

    # Any element whose class/id mentions "result" — broader net than
    # assuming a <table> at all; some platforms render results as a
    # list of <div>s instead.
    result_like = await page.locator(
        "[class*='result' i], [id*='result' i], [class*='Result']"
    ).all()
    print(f"    Elements with 'result' in class/id: {len(result_like)}")
    for i, el in enumerate(result_like[:10]):
        try:
            tag = await el.evaluate("el => el.tagName")
            cls = await el.get_attribute("class")
            id_ = await el.get_attribute("id")
            print(f"      [{i}] <{tag}> class={cls!r} id={id_!r}")
        except Exception:
            continue

    # Real pagination signals — any link/button with plausible
    # next-page text or aria-label, not assuming Fylde's exact wording.
    pagination_like = await page.locator(
        "a:has-text('Next'), button:has-text('Next'), "
        "[aria-label*='next' i], [class*='pag' i]"
    ).all()
    print(f"    Elements suggesting pagination: {len(pagination_like)}")
    for i, el in enumerate(pagination_like[:10]):
        try:
            text = (await el.inner_text()).strip()
            aria = await el.get_attribute("aria-label")
            print(f"      [{i}] visible text={text!r} aria-label={aria!r}")
        except Exception:
            continue

    # Real page title and a body-text snippet, for a human-readable
    # sanity check alongside the structural dump above.
    title = await page.title()
    body_snippet = (await page.locator("body").inner_text())[:300]
    print(f"    Real page title: {title!r}")
    print(f"    Real body text (first 300 chars): {body_snippet!r}")


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

        print(f"\nLoading {SEARCH_URL}")
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"Real URL before disclaimer: {page.url}")
        if "Disclaimer" in page.url:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                await page.click("input[value='Agree']", timeout=5_000)
            print(f"Real URL after disclaimer: {page.url}")

        await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
        await page.fill("#DateReceivedTo", end_str, timeout=5_000)
        print(f"Filled real date range: {start_str} to {end_str}")

        form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
        submit = form_loc.locator("input[type='submit']:visible")
        count = await submit.count()
        print(f"Real submit button matches within form: {count}")

        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit.first.click(timeout=5_000)
        print(f"Real post-submit URL: {page.url}")

        # First snapshot — immediately after navigation settles
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await dump_real_structure(page, "immediately after networkidle")

        # Save this snapshot regardless — real evidence either way
        html_1 = await page.content()
        with open("/tmp/bridgend_results_immediate.html", "w", encoding="utf-8") as f:
            f.write(html_1)
        await page.screenshot(path="/tmp/bridgend_results_immediate.png", full_page=True)

        # Second snapshot — after a generous extra wait, in case
        # results load/re-render via a separate JS call after the
        # initial page settles (plausible given Bridgend's form had
        # extra hidden fields — Module, SortDirection — not seen on
        # Fylde/Worcester/Vale of Glamorgan's own forms).
        print("\nWaiting an extra 5 seconds in case results load asynchronously...")
        await asyncio.sleep(5)
        await dump_real_structure(page, "after extra 5s wait")

        html_2 = await page.content()
        with open("/tmp/bridgend_results_after_wait.html", "w", encoding="utf-8") as f:
            f.write(html_2)
        await page.screenshot(path="/tmp/bridgend_results_after_wait.png", full_page=True)

        if html_1 == html_2:
            print("\nReal confirmation: HTML identical before and after the extra wait — "
                  "not an async-loading issue, whatever's on the page loaded immediately.")
        else:
            print("\nReal finding: HTML CHANGED after the extra wait — results likely "
                  "load asynchronously. The 'after_wait' files reflect the real content.")

        await context.close()
        await browser.close()

    print("\nDiagnostic complete. Check the printed structure dump above, plus the "
          "saved HTML/screenshot files, to identify Bridgend's real results markup.")


if __name__ == "__main__":
    asyncio.run(main())
