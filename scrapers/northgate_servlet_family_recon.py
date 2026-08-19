#!/usr/bin/env python3
"""
PlanFind — Northgate 'ApplicationSearchServlet' family recon tool
(2026-08-19).

Priority 2 on the roadmap — 4 councils on what looks like one shared
Northgate variant, genuinely different from the northgate_scraper.py
already built (that one only knows Runnymede/Conwy's GeneralSearch.aspx
flow — a completely different URL shape and, likely, a different real
mechanism underneath).

  - South Tyneside — NewApplicationsSearch.aspx, results at
    Generic/StdResults.aspx. Real user recon flagged this one as
    "simple search, set the date and search, applications shown next
    page, goes back 31 days" — and the original results URL captured
    contains what LOOKS like a dynamically-generated, session-specific
    XML file path (XMLtemp/<session-token>/<guid>.xml) — worth
    confirming directly rather than assuming a stable, hardcodable
    results URL exists the way it does for Idox's weeklyList. If
    genuinely dynamic per-session, that changes the whole scraper
    architecture (can't just build a URL, has to capture whatever the
    real search interaction returns).
  - Hartlepool, High Peak, Staffordshire Moorlands — identical
    ApplicationSearchServlet URL. Real user recon: Hartlepool and High
    Peak both separately expose a weekly-list-specific servlet too
    (WeeklyListServlet for High Peak; unclear yet whether Hartlepool
    has an equivalent or only the general search). Also both flag a
    SEPARATE "major/contentious developments" search — a genuinely
    different real category of applications that a full scraper should
    probably capture too, not just skip.

WHY RECON FIRST, same discipline as every other platform this session:
these are old-school Java servlet-based systems ("Servlet" in the URL
itself), a different technology generation from Idox's .do endpoints
or even Northgate's own .aspx GeneralSearch flow already handled. No
assumptions here about form field names, date format, session
handling, or result pagination — all discovered live and dumped for
direct inspection, exactly like the NI and getApplications recon did
before any scraper code got written for those.

This recon:
  1. Loads each council's real search page, dumps the real form
     structure (every input/select field, names, ids).
  2. Attempts a real search (a plausible date range) and captures
     EXACTLY what real URL and content comes back — critical for
     South Tyneside specifically, given the dynamic-XML-path concern.
  3. Where a separate weekly-list or major-developments URL exists,
     recons that too.
  4. Saves full HTML + screenshots at every real step for direct
     human inspection — no scraper code gets written until this comes
     back with real, confirmed answers.
"""
import asyncio
import re
from datetime import date, timedelta, datetime, timezone

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

# (council_name, primary search URL, optional secondary URL — real
# user-supplied URLs, not guessed)
TARGETS = [
    ("South Tyneside Council",
     "https://planning.southtyneside.info/Northgate/PlanningExplorer/NewApplicationsSearch.aspx",
     None),
    ("Hartlepool Borough Council",
     "https://planning.hartlepool.gov.uk/portal/servlets/ApplicationSearchServlet",
     "https://planning.hartlepool.gov.uk/portal/servlets/MajorContentiousDevelopmentservlet"),
    ("High Peak Borough Council",
     "http://planning.highpeak.gov.uk/portal/servlets/ApplicationSearchServlet",
     "http://planning.highpeak.gov.uk/portal/servlets/WeeklyListServlet"),
    ("Staffordshire Moorlands District Council",
     "http://publicaccess.staffsmoorlands.gov.uk/portal/servlets/ApplicationSearchServlet",
     None),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def dump_form_fields(page, council_name: str, step_label: str):
    """Real, direct form field dump — no assumptions about field names
    for a date search on a platform we've never seen before."""
    print(f"\n  Real form fields found on this page:")
    try:
        inputs = page.locator("input")
        count = await inputs.count()
        for i in range(count):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                value = await el.get_attribute("value") or ""
                if itype.lower() in ("hidden", "submit", "button", "text", "date", ""):
                    print(f"    <input> type={itype!r} name={name!r} id={el_id!r} value={value!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ input dump error: {e}")

    try:
        selects = page.locator("select")
        count = await selects.count()
        for i in range(count):
            el = selects.nth(i)
            try:
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                opt_count = await el.locator("option").count()
                print(f"    <select> name={name!r} id={el_id!r} ({opt_count} options)")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ select dump error: {e}")


async def recon_one(browser, council_name: str, primary_url: str, secondary_url: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {council_name}")
    print("=" * 70)
    print(f"Primary URL: {primary_url}")

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(primary_url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:
        print(f"  ⚠ Navigation error on primary URL: {e}")
        await context.close()
        return

    status = response.status if response else None
    print(f"  HTTP status: {status}")

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)

    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL after any redirects: {page.url}")

    html = await page.content()
    out_html = f"/tmp/northgate_servlet_recon_{slug(council_name)}_step1.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/northgate_servlet_recon_{slug(council_name)}_step1.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_form_fields(page, council_name, "step1")

    # REAL FIX (2026-08-19) — South Tyneside's search button click failed
    # 58 times in a row, every attempt blocked by the same real, named
    # overlay: <div id="ivcb-overlay">. Near-certainly a cookie-consent
    # banner sitting on top of the page. Try common real dismissal
    # patterns before attempting any further interaction, rather than
    # let the same real obstacle block every subsequent step.
    print(f"\n  Checking for a blocking overlay/cookie banner…")
    for selector in ["#ivcb-overlay button", "#ivcb-overlay .accept",
                      "button:has-text('Accept')", "button:has-text('I agree')",
                      "button:has-text('Close')", "[id*='cookie'] button"]:
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible(timeout=2000):
                await el.click(timeout=3000)
                print(f"    Dismissed an overlay via selector {selector!r}")
                await asyncio.sleep(1)
                break
        except Exception:
            continue
    # Last resort — if a real overlay is still present and blocking
    # clicks after trying real dismissal buttons, just remove it
    # directly. This is diagnostic tooling, not the real production
    # scraper — acceptable here specifically to see what's actually
    # behind it; a real scraper would need a properly targeted fix,
    # not this blunt approach.
    try:
        overlay = page.locator("#ivcb-overlay")
        if await overlay.count() > 0 and await overlay.is_visible(timeout=1000):
            await page.evaluate("document.getElementById('ivcb-overlay')?.remove()")
            print(f"    Real overlay #ivcb-overlay still present — force-removed via JS "
                  f"(diagnostic-only workaround, not how the real scraper should handle it)")
    except Exception:
        pass

    # Try a real, plausible search — a date range covering roughly the
    # last month, since we don't yet know the real expected format for
    # any of these 4 councils. Look for real date input fields by
    # common name patterns, without assuming one specific name works
    # for all 4 (these might differ council to council even within the
    # same platform).
    # REAL FIX (2026-08-19, round 3) — real evidence from the previous
    # run: filling ALL FOUR date-range pairs simultaneously (Valid,
    # Decision, AppealLodged, AppealDecision) produced a genuine, honest
    # "did not return any results" response from all 3 simple-servlet
    # councils — not a broken search, just an unrealistic combination
    # almost no real application would ever satisfy (validated AND
    # decided AND appeal-lodged AND appeal-decided, all within the same
    # 30 days). Confirmed by directly reading the real saved HTML, not
    # assumed from a results-page title alone (a real, separate mistake
    # in the previous round — a non-empty title on High Peak/
    # Staffordshire Moorlands turned out to still be the same "no
    # results" message, just with a different page title than
    # Hartlepool's identical empty-results response).
    #
    # Real fix: fill ONLY ONE canonical date-range pair, matching what
    # an actual production scraper needs (recently SUBMITTED
    # applications) — "Received" if the council's form has it (High
    # Peak, Staffordshire Moorlands both do), falling back to "Valid"
    # where it doesn't (Hartlepool has no separate ReceivedDate field
    # at all — Valid is the closest real equivalent on its own form).
    # Every other date-range field is left deliberately blank.
    print(f"\n  Attempting a real search — looking for date input fields…")
    today = date.today()
    month_ago = today - timedelta(days=30)
    date_from_str = month_ago.strftime("%d/%m/%Y")
    date_to_str = today.strftime("%d/%m/%Y")

    async def _real_field_names() -> set[str]:
        names = set()
        try:
            inputs = page.locator("input[type='text'], input[type='date'], input:not([type])")
            count = await inputs.count()
            for i in range(count):
                name = await inputs.nth(i).get_attribute("name")
                if name:
                    names.add(name)
        except Exception:
            pass
        return names

    real_names = await _real_field_names()
    target_prefix = None
    if "ReceivedDateFrom" in real_names and "ReceivedDateTo" in real_names:
        target_prefix = "ReceivedDate"
    elif "ValidDateFrom" in real_names and "ValidDateTo" in real_names:
        target_prefix = "ValidDate"

    filled_any = False
    fill_errors = []

    async def _fill_one(field_name: str, value: str) -> bool:
        el = page.locator(f"input[name='{field_name}']").first
        if await el.count() == 0:
            return False
        try:
            await el.fill(value, timeout=1500)
            print(f"    Filled real field name={field_name!r} with {value!r}")
            return True
        except Exception:
            try:
                await el.evaluate(
                    "(el, val) => { el.value = val; "
                    "el.dispatchEvent(new Event('change', {bubbles: true})); "
                    "el.dispatchEvent(new Event('blur', {bubbles: true})); }",
                    value,
                )
                print(f"    Field name={field_name!r} is a readonly date-picker "
                      f"field — set via JS + change event instead: {value!r}")
                return True
            except Exception as js_e:
                print(f"    ⚠ Field name={field_name!r} exists but could not be "
                      f"set at all: {js_e}")
                fill_errors.append((field_name, str(js_e)))
                return False

    if target_prefix:
        print(f"    Using real field '{target_prefix}' ONLY (leaving every other "
              f"date-range field blank, matching real evidence that filling "
              f"multiple pairs at once produces a genuine, honest zero-result "
              f"response, not a broken search)")
        ok_from = await _fill_one(f"{target_prefix}From", date_from_str)
        ok_to = await _fill_one(f"{target_prefix}To", date_to_str)
        filled_any = ok_from or ok_to
    else:
        print(f"    ⚠ Neither ReceivedDate nor ValidDate field pair found on "
              f"this page — real field names were: {sorted(real_names)}")
        print(f"    ⚠ {len(fill_errors)} field(s) matched a real date-field name "
              f"but couldn't actually be filled — see errors above, likely a "
              f"visibility/overlay issue same as the South Tyneside search-"
              f"button block, not a genuine 'field doesn't exist' situation.")

    if not filled_any:
        print(f"    No text-based date fields filled — trying a real "
              f"days-back/preset dropdown instead (South Tyneside's own "
              f"real UI paradigm — a 'vrDays' select, not free-text dates).")
        try:
            for select_name in ["vrDays", "days", "Days"]:
                sel = page.locator(f"select[name='{select_name}']")
                if await sel.count() > 0:
                    # Real, visible option text unknown without a fresh
                    # dump — try selecting by a plausible label first,
                    # falling back to the LAST option (often "31", the
                    # widest real range, per the recon note "goes back
                    # 31 days") if no label match is found.
                    try:
                        await sel.select_option(label="31")
                        print(f"    Selected '31' in real dropdown name={select_name!r}")
                        filled_any = True
                    except Exception:
                        try:
                            options = await sel.locator("option").all_text_contents()
                            if options:
                                await sel.select_option(label=options[-1])
                                print(f"    Selected last real option {options[-1]!r} "
                                      f"in dropdown name={select_name!r} (options were: {options})")
                                filled_any = True
                        except Exception as e2:
                            print(f"    ⚠ Could not select any option in {select_name!r}: {e2}")
                    break
        except Exception as e:
            print(f"    ⚠ Days-back dropdown handling error: {e}")

    if not filled_any:
        print(f"    ⚠ No obvious date fields found/filled — check the real form "
              f"field dump above and the saved HTML/screenshot directly.")

    # Try clicking a real, plausible submit/search button
    submitted = False
    try:
        for label_pattern in [r"^search$", r"^submit$", r"^find$", r"^go$"]:
            btn = page.get_by_role("button", name=re.compile(label_pattern, re.I))
            if await btn.count() > 0:
                await btn.first.click()
                submitted = True
                print(f"    Clicked a button matching {label_pattern!r}")
                break
        if not submitted:
            submit_input = page.locator("input[type='submit']")
            if await submit_input.count() > 0:
                await submit_input.first.click()
                submitted = True
                print(f"    Clicked an <input type='submit'>")
    except Exception as e:
        print(f"    ⚠ Submit click error: {e}")

    if submitted:
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(2)

        print(f"\n  REAL URL after search submission: {page.url}")
        results_title = await page.title()
        print(f"  Real results page title: {results_title!r}")

        results_html = await page.content()
        out_html2 = f"/tmp/northgate_servlet_recon_{slug(council_name)}_results.html"
        with open(out_html2, "w", encoding="utf-8") as f:
            f.write(results_html)
        out_png2 = f"/tmp/northgate_servlet_recon_{slug(council_name)}_results.png"
        try:
            await page.screenshot(path=out_png2, full_page=True)
        except Exception:
            pass
        print(f"  Saved: {out_html2}, {out_png2}")

        # REAL FIX (2026-08-19, round 3) — a non-empty results title
        # alone is NOT proof real data came through, confirmed the hard
        # way: High Peak and Staffordshire Moorlands both had real,
        # distinct titles last round while their actual body content
        # was the SAME honest "did not return any results" message as
        # Hartlepool's empty-titled page. Checking the real body text
        # directly and explicitly now, not inferring from the title.
        results_lower = results_html.lower()
        if "did not return any results" in results_lower or "refine your query" in results_lower:
            print(f"  ⚠ REAL EMPTY RESULT: the site gave its own honest "
                  f"'no results' response — the search mechanism itself "
                  f"worked, but this specific date range/field combination "
                  f"genuinely found nothing. Not a broken scraper.")
        else:
            records_match = re.search(r"records?\s+\d+\s+to\s+\d+\s+of\s+(\d+)", results_html, re.I)
            if records_match:
                print(f"  ✓ REAL DATA CONFIRMED: {records_match.group(0)!r} — "
                      f"genuine populated results, not just a page that loaded.")
            else:
                print(f"  ⚠ No explicit 'no results' message found, but also no "
                      f"real record-count pattern recognised — check the saved "
                      f"HTML/screenshot directly to see what's actually there.")

        # Real, direct check on the South Tyneside dynamic-XML-path
        # concern — does the real results URL contain anything that
        # looks like a session-specific temp file path?
        if "xmltemp" in page.url.lower() or re.search(r"/[a-f0-9]{8}-[a-f0-9]{4}-", page.url, re.I):
            print(f"  ⚠ FLAG: real results URL contains what looks like a "
                  f"dynamically-generated session/temp-file path — CONFIRMS "
                  f"the pre-recon concern. A hardcoded, reusable results URL "
                  f"pattern (like Idox's weeklyList) will NOT work here — the "
                  f"scraper will need to genuinely drive the search "
                  f"interaction every time, not just build a URL directly.")
    else:
        print(f"  ⚠ Could not find/click a real search button — check the "
              f"saved HTML/screenshot directly for the real form structure.")

    await context.close()

    # Recon the secondary URL too, if one exists (weekly list / major
    # developments — a real, separate search this council exposes)
    if secondary_url:
        print(f"\n  --- Secondary URL for {council_name} ---")
        print(f"  URL: {secondary_url}")
        context2 = await browser.new_context(**CONTEXT_OPTIONS)
        page2 = await context2.new_page()
        try:
            response2 = await page2.goto(secondary_url, wait_until="domcontentloaded", timeout=45_000)
            print(f"  HTTP status: {response2.status if response2 else None}")
            try:
                await page2.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1)
            title2 = await page2.title()
            print(f"  Real page title: {title2!r}")
            html2 = await page2.content()
            out_html3 = f"/tmp/northgate_servlet_recon_{slug(council_name)}_secondary.html"
            with open(out_html3, "w", encoding="utf-8") as f:
                f.write(html2)
            out_png3 = f"/tmp/northgate_servlet_recon_{slug(council_name)}_secondary.png"
            try:
                await page2.screenshot(path=out_png3, full_page=True)
            except Exception:
                pass
            print(f"  Saved: {out_html3}, {out_png3}")
            await dump_form_fields(page2, council_name, "secondary")
        except Exception as e:
            print(f"  ⚠ Navigation error on secondary URL: {e}")
        await context2.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Northgate ApplicationSearchServlet "
          f"family recon — {len(TARGETS)} councils\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for name, primary, secondary in TARGETS:
            await recon_one(browser, name, primary, secondary)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)
    print("Download the workflow artifact and read the saved HTML/screenshots")
    print("directly before writing any scraper code. In particular:")
    print("  1. Did the real search submission work for each council, and what")
    print("     did the REAL results URL look like — stable/rebuildable, or")
    print("     dynamically session-specific (the South Tyneside concern)?")
    print("  2. What are the REAL date field names/formats each council's form")
    print("     actually uses — the guessed fill attempts above may not have")
    print("     found the right fields at all for some of these.")
    print("  3. Is the real results page structure similar enough across all 4")
    print("     councils to justify one shared scraper, or different enough")
    print("     (despite the shared 'ApplicationSearchServlet' URL) to need")
    print("     separate handling per council?")
    print("  4. For Hartlepool/High Peak: does the secondary (major")
    print("     developments / weekly list) URL show a genuinely different")
    print("     real page, worth its own separate scraping pass?")


if __name__ == "__main__":
    asyncio.run(main())
