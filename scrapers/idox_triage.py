#!/usr/bin/env python3
"""
Idox triage for the 50 councils that have never saved (2026-10-06).

Fifty councils on the nightly Idox list have saved nothing in 34-117
nightly runs. The nightly log only says "timeout" or "nothing loaded"; it
doesn't say WHY, and the causes need different answers (a connection block
can't be fixed in code, a form that loads but won't submit might be).

For each council this makes ONE quick attempt (session page, then the
monthly list for last month — or the weekly list for councils configured
that way — and, if the form loads, one submit) and puts the council in a
category. It writes NOTHING to the database. Needs idox_scraper.py and
idox_councils.py alongside it.
"""
import asyncio
import re
import time
from collections import defaultdict

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

import idox_scraper as scraper            # production browser settings only
from idox_councils import IDOX_COUNCILS

NEVER_WORKED = [
    "Harborough District Council", "Sandwell Metropolitan Borough Council", "North Northamptonshire Council",
    "Calderdale Metropolitan Borough Council", "Wolverhampton City Council", "Shropshire Council",
    "Aberdeen City Council", "Breckland District Council", "City of Westminster", "Clackmannanshire Council",
    "Glasgow City Council", "Hart District Council", "London Borough of Bexley", "London Borough of Enfield",
    "London Borough of Hammersmith and Fulham", "London Borough of Kingston upon Thames",
    "London Borough of Southwark", "London Borough of Sutton", "Somerset Council (Mendip)",
    "South Ayrshire Council", "Tendring District Council", "Torridge District Council",
    "Cambridgeshire County Council", "Cardiff Council", "Carmarthenshire County Council", "Newport City Council",
    "North Yorkshire Council", "Oxford City Council", "Sevenoaks District Council",
    "South Downs National Park Authority", "Southampton City Council", "Swansea Council", "West Berkshire Council",
    "Rother District Council", "Wealden District Council", "Midlothian Council", "Braintree District Council",
    "South Gloucestershire Council", "North Lanarkshire Council", "Scottish Borders Council",
    "Harlow District Council", "Uttlesford District Council", "Sunderland City Council",
    "Three Rivers District Council", "London Borough of Newham", "Gloucester City Council",
    "Watford Borough Council", "Aberdeenshire Council", "East Riding of Yorkshire Council", "Stafford Borough Council",
]
CONCURRENCY = 3
MONTH_INDEX = 1

# Copied from idox_scraper.py, in the order the production scraper tries them.
DROPDOWN_SELECTORS = ["select[id='month']", "select[name='month']",
                      "select[id='searchCriteria.monthYearIndex']", "select[name='searchCriteria.monthYearIndex']",
                      "select[name*='monthYear']", "select[id*='monthYear']"]
RADIO_SELECTORS = ["input#dateReceived", "input[value='dateReceived']", "input[id*='Received'][type='radio']",
                   "input[name*='date'][value*='eceiv']", "label:has-text('Received') input", "input[value='dc']",
                   "input[value='DC']", "input[value='dv']", "input[value='DV']",
                   "input[id*='Validated'][type='radio']", "label:has-text('Validated') input"]
SUBMIT_SELECTORS = ["#monthlyListForm input[type='submit']", "#monthlyListForm input.button",
                    "form input[type='submit']", "form button[type='submit']", "input.button"]
RESULTS_SELECTOR = "ul.searchresults, #searchResultsContainer, .searchresults, .no-results, #searchResultsForm"

ACTION = {
    "WORKS": "portal works — find out why production saved nothing",
    "WORKS_EMPTY": "results page loads with no items — check the month; probably fine",
    "FORM_NO_RESULTS": "form loads but never returns results — possibly fixable (like Blaby/Hinckley)",
    "CONNECTION_BLOCKED": "no connection from the runner — park as manual_link; ask the council to allow-list",
    "CHALLENGE_PAGE": "bot-verification page — park as manual_link; ask the council",
    "RATE_LIMITED": "rate-limited — may recover with slower pacing; retry later",
    "ERROR_PAGE": "portal answers with an error page — check the URL/path",
    "HTTP_ERROR": "HTTP error status — check the URL/path",
    "DEAD_HOST": "host does not exist — needs fresh research",
    "TLS_PROBLEM": "certificate/TLS problem on the council's side — park as manual_link",
    "NO_FORM": "page loads but has no month form — inspect by hand",
    "OTHER_ERROR": "unclassified error — read the note",
}


def early_category(error, status, title, body):
    """Everything decidable before looking for a form or results."""
    if error:
        e = error
        if "ERR_NAME_NOT_RESOLVED" in e:
            return "DEAD_HOST"
        if "ERR_CERT" in e or "ERR_SSL" in e:
            return "TLS_PROBLEM"
        if any(x in e for x in ("ERR_CONNECTION", "ERR_TIMED_OUT", "ERR_EMPTY_RESPONSE", "ERR_HTTP2", "Timeout")):
            return "CONNECTION_BLOCKED"
        return "OTHER_ERROR"
    t, b = (title or "").lower(), (body or "").lower()
    if "just a moment" in t or "performing security verification" in b or "attention required" in t:
        return "CHALLENGE_PAGE"
    if status == 429 or "too many requests" in t or "unusual traffic" in b:
        return "RATE_LIMITED"
    if status and status >= 400:
        return "HTTP_ERROR"
    if t.strip() == "error" or "server problem" in b:
        return "ERROR_PAGE"
    return None


def classify(error, status, title, body, has_form, results_found, items):
    early = early_category(error, status, title, body)
    if early:
        return early
    if items > 0:
        return "WORKS"
    if results_found:
        return "WORKS_EMPTY"
    if has_form:
        return "FORM_NO_RESULTS"
    return "NO_FORM"


def entry_for(name):
    for e in IDOX_COUNCILS:
        if e[0] == name:
            weekly = len(e) >= 3 and e[2] == "weekly"
            return e[1], (e[3] if len(e) == 4 else ""), weekly
    return None


async def probe(browser, sem, name):
    async with sem:
        got = entry_for(name)
        if got is None:
            return name, None, {"category": "OTHER_ERROR", "note": "not in IDOX_COUNCILS", "secs": 0}
        url, extra, weekly = got
        context = await browser.new_context(**scraper.CONTEXT_OPTIONS)
        page = await context.new_page()
        t0 = time.monotonic()
        error = status = None
        title = body = ""
        has_form = results_found = False
        items = 0
        try:
            try:
                await page.goto(f"{url}/search.do?action=simple&searchType=Application",
                                wait_until="domcontentloaded", timeout=25_000)
                await asyncio.sleep(1)
            except Exception:
                pass
            target = (f"{url}/weeklyListResults.do?action=firstPage" if weekly else
                      f"{url}/search.do?action=monthlyList&searchCriteria.monthYearIndex={MONTH_INDEX}"
                      f"&searchType=Application{extra}")
            resp = await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            status = resp.status if resp else None
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except PlaywrightTimeout:
                pass
            title = await page.title()
            body = (await page.locator("body").inner_text())[:3000]

            if early_category(None, status, title, body) is None:
                if not weekly:
                    for sel in DROPDOWN_SELECTORS:
                        if await page.locator(sel).count() > 0:
                            has_form = True
                            try:
                                await page.locator(sel).first.select_option(index=MONTH_INDEX)
                            except Exception:
                                pass
                            break
                    if has_form:
                        for sel in RADIO_SELECTORS:
                            if await page.locator(sel).count() > 0:
                                try:
                                    await page.locator(sel).first.click(force=True, timeout=3_000)
                                except Exception:
                                    pass
                                break
                        for sel in SUBMIT_SELECTORS:
                            if await page.locator(sel).count() > 0:
                                await page.locator(sel).first.evaluate("el => el.click()")
                                break
                try:
                    await page.wait_for_selector(RESULTS_SELECTOR, timeout=20_000)
                    results_found = True
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1)
                items = await page.locator("li.searchresult").count()
                title = await page.title()
                body = (await page.locator("body").inner_text())[:3000]
        except Exception as e:
            error = str(e)
        finally:
            await context.close()
        cat = classify(error, status, title, body, has_form, results_found, items)
        note = (error or f"title={title[:50]!r} status={status} items={items}")[:140].replace("\n", " ")
        return name, url, {"category": cat, "note": note, "secs": time.monotonic() - t0}


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\nTriaging {len(NEVER_WORKED)} councils "
              f"({CONCURRENCY} at a time)…\n")
        sem = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(*[probe(browser, sem, n) for n in NEVER_WORKED])
        await browser.close()

    by_cat = defaultdict(list)
    for name, url, r in results:
        by_cat[r["category"]].append((name, url, r))
    print(f"{'=' * 60}\nSUMMARY BY CATEGORY\n{'=' * 60}")
    for cat, rows in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{cat}  ({len(rows)})  —  {ACTION.get(cat, '')}")
        for name, url, r in sorted(rows):
            print(f"   {name:44} {r['secs']:4.0f}s  {r['note']}")
    print(f"\nTriage complete (nothing was written to the database).")


if __name__ == "__main__":
    asyncio.run(main())
