#!/usr/bin/env python3
"""
PlanFind — Telford aspxerrorpath diagnostic (2026-09-10).

Real question: telford_scraper.py's search submission consistently
redirects to a URL containing ?aspxerrorpath=..., which is IIS's
customErrors mechanism specifically for an UNHANDLED SERVER-SIDE
EXCEPTION during postback processing — a different failure category
than a WAF block or a wrong CSS selector. Two plausible real causes,
tested directly here rather than guessed at:

  1. A JS datepicker widget attached to the date fields, whose own
     change/blur handlers (or a companion hidden field it maintains)
     never fire because page.fill() sets the value in one bulk
     operation rather than simulating real keystrokes/interaction.
  2. A date format mismatch the server-side handler doesn't expect.

This script, in order:
  - Loads the real search page and dumps the actual HTML around both
    date fields (looking for datepicker library markers, companion
    hidden fields, or JS event bindings)
  - Checks whether __VIEWSTATE / __EVENTVALIDATION are present and
    non-empty before any interaction (a real, simple sanity check —
    if these are missing/empty even on initial load, that's a
    different, more basic problem)
  - Fills the date fields TWO different ways in two separate page
    loads for a clean comparison: the existing page.fill() approach,
    and page.type() (real character-by-character keystrokes, firing
    proper keydown/keyup/input events per character — the standard
    fix for finicky JS-bound fields that don't respond to a bulk
    value-set)
  - For each, screenshots the filled state before submitting, then
    submits and reports the real post-submit URL and any error detail
    the response leaks
"""
import asyncio
import sys
from datetime import date, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from telford_councils import SEARCH_URL

DATE_FROM_SEL = "#ctl00_ContentPlaceHolder1_DCdatefrom"
DATE_TO_SEL = "#ctl00_ContentPlaceHolder1_DCdateto"
SUBMIT_SEL = "#ctl00_ContentPlaceHolder1_btnSearchPlanningDetails"


async def inspect_date_field_markup(page, label: str):
    """Real evidence: dump the actual HTML immediately around each date
    field, looking for datepicker library markers (jQuery UI classes,
    data- attributes, a calendar icon/trigger element) or any companion
    hidden input whose name suggests it's tied to the same field."""
    for sel, name in [(DATE_FROM_SEL, "DCdatefrom"), (DATE_TO_SEL, "DCdateto")]:
        try:
            outer_html = await page.eval_on_selector(
                sel,
                "el => el.outerHTML"
            )
            print(f"    [{label}] {name} outerHTML: {outer_html!r}")
            # Check the parent container too — datepicker trigger icons
            # and companion hidden fields are usually siblings, not
            # inside the input itself.
            parent_html = await page.eval_on_selector(
                sel,
                "el => el.parentElement ? el.parentElement.outerHTML : '(no parent)'"
            )
            print(f"    [{label}] {name} parent outerHTML (first 800 chars): "
                  f"{parent_html[:800]!r}")
        except Exception as e:
            print(f"    [{label}] {name}: could not inspect — {e}")


async def check_viewstate(page, label: str):
    """Real sanity check — if __VIEWSTATE/__EVENTVALIDATION are missing
    or empty even before any interaction, that's a more basic problem
    than a datepicker/format issue."""
    for field in ["__VIEWSTATE", "__EVENTVALIDATION"]:
        try:
            value = await page.eval_on_selector(
                f"input[name='{field}']",
                "el => el.value"
            )
            print(f"    [{label}] {field}: present, length={len(value)}")
        except Exception:
            print(f"    [{label}] {field}: NOT FOUND on page")


async def try_fill_method(method: str, start_str: str, end_str: str):
    """Runs one full attempt (page load -> fill -> submit) using either
    'fill' (page.fill(), the existing scraper's approach) or 'type'
    (page.type(), real per-character keystrokes) — kept as two fully
    separate page loads/contexts for a clean, uncontaminated
    comparison rather than reusing state between them."""
    print(f"\n{'=' * 60}")
    print(f"METHOD: {method}")
    print(f"{'=' * 60}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True,
                                            args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
            locale="en-GB",
            ignore_https_errors=True,
        )
        page = await context.new_page()

        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"    ⚠ Page load failed: {e}")
            await context.close()
            await browser.close()
            return

        # Real evidence gathering, before any interaction
        await inspect_date_field_markup(page, method)
        await check_viewstate(page, f"{method} (before fill)")

        try:
            if method == "fill":
                await page.fill(DATE_FROM_SEL, start_str, timeout=5_000)
                await page.fill(DATE_TO_SEL, end_str, timeout=5_000)
            elif method == "type":
                # Real character-by-character typing — clears the field
                # first (click + select-all + delete) then types with a
                # small per-character delay, firing genuine
                # keydown/keyup/input events the way a real user would,
                # which is what a JS-bound datepicker's own listeners
                # are actually watching for.
                for sel, value in [(DATE_FROM_SEL, start_str), (DATE_TO_SEL, end_str)]:
                    await page.click(sel)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Delete")
                    await page.type(sel, value, delay=80)
                    # Explicit blur — some datepickers only commit/
                    # validate their state on blur, not on every
                    # keystroke.
                    await page.keyboard.press("Tab")
        except Exception as e:
            print(f"    ⚠ Fill/type failed: {e}")
            await context.close()
            await browser.close()
            return

        # Confirm what's actually IN the field after filling — the DOM
        # value, not just what we intended to set.
        for sel, name in [(DATE_FROM_SEL, "from"), (DATE_TO_SEL, "to")]:
            actual_value = await page.eval_on_selector(sel, "el => el.value")
            print(f"    [{method}] {name} field actual DOM value after fill: {actual_value!r}")

        await page.screenshot(path=f"/tmp/telford_diag_{method}_filled.png")

        try:
            submit = page.locator(SUBMIT_SEL)
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await submit.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"    ⚠ Submit/navigation failed: {e}")
            await context.close()
            await browser.close()
            return

        post_url = page.url
        print(f"    [{method}] Post-submit URL: {post_url}")
        is_error = "aspxerrorpath" in post_url
        print(f"    [{method}] Hit error redirect: {is_error}")

        if is_error:
            # Real evidence: some ASP.NET custom error pages still leak
            # a stack trace or exception message in an HTML comment or
            # a hidden diagnostic element, even with customErrors mode
            # on. Worth checking directly rather than assuming nothing
            # useful is there.
            body_text = await page.locator("body").inner_text()
            print(f"    [{method}] Error page body (first 1000 chars): "
                  f"{body_text[:1000]!r}")
            html = await page.content()
            if "<!--" in html:
                import re
                comments = re.findall(r"<!--(.*?)-->", html, re.DOTALL)
                real_comments = [c.strip() for c in comments if c.strip()]
                if real_comments:
                    print(f"    [{method}] Real HTML comments found on error page: "
                          f"{real_comments[:5]!r}")
        else:
            html = await page.content()
            print(f"    [{method}] SUCCESS — no error redirect. Real results "
                  f"page length: {len(html)} chars")
            await page.screenshot(path=f"/tmp/telford_diag_{method}_results.png")

        await context.close()
        await browser.close()


async def main():
    today = date.today()
    start = today - timedelta(days=30)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    print(f"Testing date range: {start_str} to {end_str}\n")

    await try_fill_method("fill", start_str, end_str)
    await try_fill_method("type", start_str, end_str)

    print(f"\n{'=' * 60}")
    print("Screenshots saved to /tmp/telford_diag_*.png for visual comparison")


if __name__ == "__main__":
    asyncio.run(main())
