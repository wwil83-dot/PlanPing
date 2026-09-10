#!/usr/bin/env python3
"""
PlanFind — Telford real calendar-click diagnostic (2026-09-10, round 2).

Round 1 (telford_aspxerror_diagnostic.py) ruled out a simple format
mismatch: page.fill() put a perfectly well-formed '11/08/2026' into the
date field and STILL hit the aspxerrorpath redirect. It also found the
field genuinely has class="hasDatepicker" — a real jQuery UI datepicker
widget is attached, confirming that part of the theory. Real remaining
question: does the server actually need the widget's OWN internal
state (set only by a genuine click through the calendar popup, not by
typing or setting the input's raw value), separate from what's visibly
in the text field?

This tests that directly — opens the real calendar popup via a real
click on the input, navigates months via real clicks on the
prev/next arrows (jQuery UI's standard .ui-datepicker-prev/-next
elements), and clicks the actual day cell, for both date fields. This
fires every real DOM event a genuine user interaction would, with no
assumptions about what the widget's internal JS API does or doesn't
trigger on its own.
"""
import asyncio
import re
from datetime import date, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from telford_councils import SEARCH_URL

DATE_FROM_SEL = "#ctl00_ContentPlaceHolder1_DCdatefrom"
DATE_TO_SEL = "#ctl00_ContentPlaceHolder1_DCdateto"
SUBMIT_SEL = "#ctl00_ContentPlaceHolder1_btnSearchPlanningDetails"
DATEPICKER_POPUP = "#ui-datepicker-div"


def re_exact(text: str):
    """Playwright's has_text does substring matching by default, which
    would wrongly match e.g. '1' inside '11' or '21' — need an exact
    match for day numbers specifically."""
    return re.compile(rf"^{re.escape(text)}$")


async def pick_date_via_calendar(page, field_sel: str, target: date, label: str):
    """Opens the real jQuery UI calendar popup for field_sel by clicking
    it, navigates to target's month/year via real clicks on the
    standard .ui-datepicker-prev/-next arrows, then clicks the actual
    day cell. Raises if the popup or expected controls aren't found —
    deliberately not falling back to anything else, since the whole
    point is confirming genuine calendar interaction works."""
    await page.click(field_sel)
    await page.wait_for_selector(DATEPICKER_POPUP, state="visible", timeout=10_000)

    for _ in range(24):  # safety cap — should never need more than a
                          # handful of clicks for a 30-day lookback
        header_text = await page.locator(
            f"{DATEPICKER_POPUP} .ui-datepicker-title"
        ).inner_text()
        print(f"    [{label}] Calendar currently showing: {header_text!r}")

        current_month_year = header_text.strip()
        target_month_year = target.strftime("%B %Y")
        if current_month_year == target_month_year:
            break

        if target < date.today():
            await page.click(f"{DATEPICKER_POPUP} .ui-datepicker-prev")
        else:
            await page.click(f"{DATEPICKER_POPUP} .ui-datepicker-next")
        await asyncio.sleep(0.3)
    else:
        raise RuntimeError(f"Could not navigate calendar to {target.strftime('%B %Y')} "
                            f"after 24 attempts")

    day_str = str(target.day)
    day_link = page.locator(
        f"{DATEPICKER_POPUP} table.ui-datepicker-calendar a"
    ).filter(has_text=re_exact(day_str))
    await day_link.click()
    await asyncio.sleep(0.3)


async def main():
    today = date.today()
    start = today - timedelta(days=30)
    print(f"Testing date range via real calendar clicks: "
          f"{start.strftime('%d/%m/%Y')} to {today.strftime('%d/%m/%Y')}\n")

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
            print(f"⚠ Page load failed: {e}")
            await context.close()
            await browser.close()
            return

        try:
            print("Picking FROM date via real calendar click...")
            await pick_date_via_calendar(page, DATE_FROM_SEL, start, "from")
            from_value = await page.eval_on_selector(DATE_FROM_SEL, "el => el.value")
            print(f"  -> field value after calendar pick: {from_value!r}\n")

            print("Picking TO date via real calendar click...")
            await pick_date_via_calendar(page, DATE_TO_SEL, today, "to")
            to_value = await page.eval_on_selector(DATE_TO_SEL, "el => el.value")
            print(f"  -> field value after calendar pick: {to_value!r}\n")
        except Exception as e:
            print(f"⚠ Calendar interaction failed: {e}")
            await page.screenshot(path="/tmp/telford_calendar_diag_failure.png")
            await context.close()
            await browser.close()
            return

        await page.screenshot(path="/tmp/telford_calendar_diag_filled.png")

        try:
            submit = page.locator(SUBMIT_SEL)
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await submit.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Submit/navigation failed: {e}")
            await context.close()
            await browser.close()
            return

        post_url = page.url
        print(f"Post-submit URL: {post_url}")
        is_error = "aspxerrorpath" in post_url
        print(f"Hit error redirect: {is_error}")

        if is_error:
            body_text = await page.locator("body").inner_text()
            print(f"Error page body (first 1000 chars): {body_text[:1000]!r}")
        else:
            html = await page.content()
            print(f"SUCCESS — no error redirect. Real results page length: "
                  f"{len(html)} chars")
            await page.screenshot(path="/tmp/telford_calendar_diag_results.png")
            print(f"Results page body (first 1500 chars): "
                  f"{(await page.locator('body').inner_text())[:1500]!r}")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
