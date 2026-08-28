#!/usr/bin/env python3
"""
PlanFind — Charnwood GUID stability + pagination test (2026-08-28).

Real, confirmed via charnwood_search_recon.py: a real detail URL
exists (OnlinePlanningOverview?applicationNumber=X&guid=Y) and real,
JS-click-based pagination (PagingClick('N'), 0-indexed, confirmed
PageCount=9, PageSize=20, TotalRecords=175 for July 2026).

Testing two things directly:
  1. Whether the real guid parameter is stable/reusable in a
     completely FRESH browser session (simulating a later recheck
     run), or session-bound like Barrow's — this determines whether a
     pending-recheck mechanism is possible here at all.
  2. Whether clicking through to page 2 of the real Monthly List
     results works as expected.
"""
import asyncio
from datetime import datetime, timezone

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

# Real, confirmed URL from the earlier successful search's first result
DETAIL_URL = (
    "https://planningexplorer.charnwood.gov.uk/Assure/ES/Presentation/"
    "Planning/OnlinePlanning/OnlinePlanningOverview"
    "?applicationNumber=P%2F26%2F1328%2F2"
    "&guid=f324314e-bac3-4c5b-84e4-8757d9e578a1"
)


async def test_guid_stability(browser):
    print(f"{'=' * 70}")
    print("TEST 1: Real GUID stability in a completely fresh session")
    print("=" * 70)

    # Real, deliberate fresh context — no cookies/session carried over
    # from any earlier search, simulating exactly what a later,
    # separate recheck run would look like
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    print(f"Testing real URL directly in a fresh session:\n{DETAIL_URL}\n")

    try:
        response = await page.goto(DETAIL_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"⚠ Navigation error: {e}")
        await context.close()
        return

    print(f"Real HTTP status: {response.status if response else None}")
    print(f"Real final URL: {page.url}")
    title = await page.title()
    print(f"Real page title: {title!r}\n")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1500]
    except Exception:
        pass
    print(f"Real visible body text (first 1500 chars): {body_text!r}\n")

    await context.close()


async def test_pagination(browser):
    print(f"\n{'=' * 70}")
    print("TEST 2: Real pagination — clicking to page 2")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    URL = "https://planningexplorer.charnwood.gov.uk/Assure/ES/Presentation/Planning/OnLinePlanning/OnlinePlanningSearch"
    try:
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"⚠ Navigation error: {e}")
        await context.close()
        return

    try:
        await page.locator("#PlanningApplications").check(timeout=5_000)
        await page.get_by_text("Past month", exact=True).first.click(timeout=5_000)
        await page.get_by_text("Weekly / Monthly list", exact=True).first.click(timeout=8_000)
        await asyncio.sleep(1)
        await page.get_by_text("Monthly list", exact=True).first.click(timeout=8_000)
        await asyncio.sleep(1)

        month_select = page.locator("select").filter(has=page.locator("option", has_text="2026"))
        options = await month_select.first.locator("option").all_text_contents()
        await month_select.first.select_option(label=options[1], timeout=5_000)
        # REAL, CONFIRMED via direct manual testing: the working flow
        # leaves this checkbox unchecked — not needed.
        # await page.get_by_text("Validated this month", exact=True).first.click(timeout=5_000)
        await page.locator("#ancWeeklyMonthlySearch").first.click(timeout=8_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        # REAL FIX — confirmed necessary via repeated inconsistent
        # behaviour across runs: "networkidle" only tracks network
        # requests finishing, not whether this platform's own JS has
        # finished re-rendering a potentially large dataset (175 real
        # rows worth of underlying data) into the DOM afterward — a
        # genuine race condition, not a logic error. Polling for the
        # real "N Results" text to actually appear, rather than
        # trusting a fixed sleep to happen to land after it does.
        import re
        real_total_this_run = None
        for attempt in range(15):
            try:
                body_text_check = await page.locator("body").inner_text()
            except Exception:
                body_text_check = ""
            m = re.search(r"(\d+) Results", body_text_check)
            if m:
                real_total_this_run = int(m.group(1))
                print(f"Real results text appeared after {attempt + 1}s: "
                      f"{real_total_this_run} Results")
                break
            await asyncio.sleep(1)
        if real_total_this_run is None:
            print("⚠ Real results text never appeared after 15s of polling")

        print("Reached real search results\n")
    except Exception as e:
        print(f"⚠ Could not reach search results: {e}")
        await context.close()
        return

    # REAL FIX — confirmed via direct HTML inspection: the exact
    # onclick attribute value IS present as expected
    # ("$('#CurrentPageIndex').val(1); PagingClick('1');"), yet the
    # earlier nested-quote CSS attribute selector
    # (a[onclick*="PagingClick('1')"]) found zero matches — likely a
    # real CSS-selector parsing issue mixing double and single quotes.
    # Using a safer, simpler text-based match scoped to the confirmed
    # real pagination container instead.
    try:
        pagination = page.locator("ul.tablePagingRow")
        pcount = await pagination.count()
        print(f"Real ul.tablePagingRow containers found: {pcount}")
        if pcount == 0:
            if real_total_this_run is not None and real_total_this_run <= 20:
                print(f"✓ Genuinely benign — {real_total_this_run} results fits on one page, "
                      f"no pagination controls needed at all")
            else:
                print("⚠ The pagination container itself was not found, despite "
                      f"{real_total_this_run} results — genuinely unexpected")
            await context.close()
            return

        # Real, genuinely different approach — confirmed via 2 prior
        # failed attempts that a real UI click on the page-2 link
        # mechanically fires with no error, yet the page's AJAX-driven
        # content swap never actually happens (or happens outside
        # whatever window a fixed sleep catches). Calling the exact
        # real underlying JS directly instead, replicating both real
        # statements from the confirmed onclick handler
        # ("$('#CurrentPageIndex').val(1); PagingClick('1');"),
        # bypassing Playwright's own click machinery entirely.
        first_ref_before = await page.locator("table").nth(1).locator("tr").nth(1).inner_text()
        print(f"Real first-row reference before: {first_ref_before[:50]!r}")

        await page.evaluate("$('#CurrentPageIndex').val(1); PagingClick('1');")
        print("Called real PagingClick('1') directly via JS")

        # Real, more robust wait: poll for the actual content to
        # change, rather than trust a fixed sleep to happen to land
        # inside whatever window the AJAX update takes
        changed = False
        for attempt in range(10):
            await asyncio.sleep(1)
            try:
                first_ref_after = await page.locator("table").nth(1).locator("tr").nth(1).inner_text()
            except Exception:
                continue
            if first_ref_after != first_ref_before:
                changed = True
                print(f"Real content changed after {attempt + 1}s: {first_ref_after[:50]!r}")
                break
        if not changed:
            print("⚠ Real content still unchanged after 10s of polling")
    except Exception as e:
        print(f"⚠ Could not call PagingClick directly: {e}")
        await context.close()
        return

    from bs4 import BeautifulSoup
    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) > 1:
        rows = tables[1].find_all("tr")
        print(f"Real rows on page 2: {len(rows)}")
        if len(rows) > 1:
            first_ref = rows[1].get_text(" ", strip=True)[:200]
            print(f"First real data row on page 2: {first_ref}")
            # Real, explicit confirmation the click genuinely worked
            print(f"\nReal confirmation: {'DIFFERENT from page 1 (P/26/1328/2) — click worked' if 'P/26/1328/2' not in first_ref else '⚠ SAME as page 1 — click may have silently failed'}")

    out_html = "/tmp/charnwood_page2.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved: {out_html}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Charnwood GUID stability + pagination test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        await test_guid_stability(browser)
        await test_pagination(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
