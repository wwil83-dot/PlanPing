#!/usr/bin/env python3
"""
PlanFind — Telford real results-page structure diagnostic (2026-09-10,
round 4).

Round 3 confirmed the real fix: plain page.fill() with DASH-formatted
dates ('%d-%m-%Y', not '%d/%m/%Y') succeeds with no calendar
interaction needed at all. This uses that confirmed-working submission,
then extracts the REAL markup around the pagination controls
("Showing 1 to 10 of 58 items", "1 2 3 4 5 6 of 6 Next") and the two
real result tabs ("Registered (58)", "Determined (85)") — needed to
build a real production parser rather than guessing at selectors the
same way the calendar navigation was initially guessed wrong.

Saves the full page HTML to /tmp for upload as an artifact, and prints
targeted excerpts around each real label directly in the log so the
key selectors can often be read straight from the log without needing
to open the file.
"""
import asyncio
import re
from datetime import date, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from telford_councils import SEARCH_URL

DATE_FROM_SEL = "#ctl00_ContentPlaceHolder1_DCdatefrom"
DATE_TO_SEL = "#ctl00_ContentPlaceHolder1_DCdateto"
SUBMIT_SEL = "#ctl00_ContentPlaceHolder1_btnSearchPlanningDetails"


def print_context_around(html: str, label: str, needle: str, window: int = 600):
    """Finds the real, first occurrence of needle in html and prints
    the surrounding raw markup — enough real context to identify the
    actual clickable element (tag, class, id, href/onclick) without
    guessing."""
    idx = html.find(needle)
    if idx == -1:
        print(f"    [{label}] NOT FOUND in HTML: {needle!r}")
        return
    start = max(0, idx - window // 2)
    end = min(len(html), idx + len(needle) + window // 2)
    print(f"    [{label}] Real markup around {needle!r}:")
    print(f"    {html[start:end]!r}\n")


async def main():
    today = date.today()
    start = today - timedelta(days=30)

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
            # Confirmed-working fix from round 3 — dash format, plain fill.
            await page.fill(DATE_FROM_SEL, start.strftime("%d-%m-%Y"), timeout=5_000)
            await page.fill(DATE_TO_SEL, today.strftime("%d-%m-%Y"), timeout=5_000)

            submit = page.locator(SUBMIT_SEL)
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await submit.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Search failed: {e}")
            await context.close()
            await browser.close()
            return

        if "aspxerrorpath" in page.url:
            print(f"⚠ Still hit error redirect: {page.url} — real fix may have "
                  f"regressed, stopping here.")
            await context.close()
            await browser.close()
            return

        print(f"Real results URL: {page.url}\n")
        html = await page.content()

        with open("/tmp/telford_results_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Full HTML saved to /tmp/telford_results_page.html "
              f"({len(html)} chars)\n")

        print("=" * 60)
        print("REAL MARKUP AROUND KEY LABELS")
        print("=" * 60)
        print_context_around(html, "top tabs", "Planning Applications (")
        # REAL FIX (round 5) — searching for the count-attached string
        # ('Registered (58)') never matches raw HTML, confirmed by the
        # "Planning Applications" tab's own real markup: the count sits
        # inside its own <span>, with a tag boundary between "(" and
        # the number. Only the flattened inner_text() concatenates them
        # into one continuous string. Searching for the label alone
        # (no count, no parenthesis) is the real fix.
        print_context_around(html, "status tabs", "Registered")
        print_context_around(html, "status tabs", "Determined")
        print_context_around(html, "pagination", "PageSizeDropDownTop")

        # Real test: does selecting page size 100 eliminate the need
        # for "Next" clicking at all, on either the Registered (58) or
        # Determined (85) tab — both fit under 100.
        print("\n" + "=" * 60)
        print("TESTING: select page size 100, check if pagination disappears")
        print("=" * 60)
        try:
            size_select = page.locator(
                "#ctl00_ContentPlaceHolder1_gvResults_ctl01_PageSizeDropDownTop"
            )
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await size_select.select_option(value="100")
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            html_after_resize = await page.content()
            body_text_after = await page.locator("body").inner_text()
            has_next_link = "PagerTopNext" in html_after_resize
            print(f"Real body text after selecting page size 100 (first 800 chars): "
                  f"{body_text_after[:800]!r}")
            print(f"\nStill has a 'Next' pagination link present: {has_next_link}")
            with open("/tmp/telford_results_page_size100.html", "w", encoding="utf-8") as f:
                f.write(html_after_resize)
            print(f"Full HTML after resize saved to "
                  f"/tmp/telford_results_page_size100.html ({len(html_after_resize)} chars)")
        except Exception as e:
            print(f"⚠ Page size selection failed: {e}")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
