#!/usr/bin/env python3
"""
Medway "Decided" list + application-page probe (2026-10-06).

Findings so far: Medway's Idox list pages say "Status: Decided" with no
outcome (seen in a screenshot of its weekly Decided list), so the
production scraper — which only ever selects the received/validated list —
labels everything 'pending'. Medway's list form offers a Validated /
Decided radio choice.

This answers, for last month, without writing anything:
  1. what the Validated/Decided radios really are (ids, values, labels)
     and how to select "Decided" the way a script can
  2. how many applications the Decided list returns, and what each result
     line says
  3. whether each application's OWN page carries the outcome and a
     decision date (the first few are opened and their summary table is
     printed in full)
"""
import asyncio
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

import idox_scraper as scraper            # only for the production browser settings

BASE = "https://publicaccess1.medway.gov.uk/online-applications"
MONTH_INDEX = 1            # last month: complete, so plenty of decisions
DETAIL_SAMPLES = 5

DROPDOWN_SELECTORS = ["select[id='month']", "select[name='month']",
                      "select[id='searchCriteria.monthYearIndex']", "select[name*='monthYear']"]
SUBMIT_SELECTORS = ["#monthlyListForm input[type='submit']", "#monthlyListForm input.button",
                    "form input[type='submit']", "form button[type='submit']", "input.button"]
RESULTS_SELECTOR = ("ul.searchresults, #searchResultsContainer, .searchresults, "
                    ".no-results, #searchResultsForm")
TOTAL_RE = re.compile(r"Showing\s+\d+\s*[-–]\s*\d+\s+of\s+(\d+)", re.I)
INTERESTING_RE = re.compile(r"decis|status|appeal|issued|determin", re.I)

RADIO_JS = """el => {
  let label = '';
  if (el.labels && el.labels.length) label = el.labels[0].textContent;
  else if (el.closest('label')) label = el.closest('label').textContent;
  else if (el.nextSibling && el.nextSibling.textContent) label = el.nextSibling.textContent;
  return {id: el.id, name: el.name, value: el.value, label: (label || '').trim().slice(0, 40),
          checked: el.checked, inLayout: el.offsetParent !== null};
}"""


def parse_total(body: str):
    m = TOTAL_RE.search(body or "")
    return int(m.group(1)) if m else None


def choose_decided(radios: list[dict]):
    """The radio whose label or value says 'decided' (and not 'validated')."""
    for r in radios:
        text = f"{r.get('label', '')} {r.get('value', '')}".lower()
        if "decid" in text and "valid" not in text:
            return r
    return None


def detail_pairs(html: str) -> list[tuple[str, str]]:
    """label/value rows from an Idox application summary table."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="simpleDetailsTable") or soup.find("table")
    pairs = []
    if table:
        for tr in table.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                pairs.append((th.get_text(" ", strip=True), td.get_text(" ", strip=True)))
    return pairs


def interesting(pairs):
    return [(k, v) for k, v in pairs if INTERESTING_RE.search(k)]


async def settle(page, extra=1.5):
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**scraper.CONTEXT_OPTIONS)
        page = await context.new_page()

        # session first, then the monthly list — exactly as production does
        try:
            await page.goto(f"{BASE}/search.do?action=simple&searchType=Application",
                            wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(1)
        except Exception:
            pass
        await page.goto(f"{BASE}/search.do?action=monthlyList"
                        f"&searchCriteria.monthYearIndex={MONTH_INDEX}&searchType=Application",
                        wait_until="domcontentloaded", timeout=45_000)
        await settle(page)

        print("=" * 60 + "\nSTEP 1: the radios on the monthly list form\n" + "=" * 60)
        print(f"  title: {await page.title()!r}\n  url:   {page.url}")
        radios = []
        loc = page.locator("input[type='radio']")
        for i in range(await loc.count()):
            radios.append(await loc.nth(i).evaluate(RADIO_JS))
        for r in radios:
            print(f"   {r}")
        decided = choose_decided(radios)
        if decided is None:
            print("  ⚠ no radio looks like 'Decided' — stopping; the list above shows what exists")
            await browser.close()
            return
        print(f"\n  -> using the 'Decided' radio: id={decided['id']!r} value={decided['value']!r}")

        print("\n" + "=" * 60 + "\nSTEP 2: run the Decided list for last month\n" + "=" * 60)
        for sel in DROPDOWN_SELECTORS:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).select_option(index=MONTH_INDEX)
                print(f"  month dropdown set via {sel!r}")
                break
        radio_sel = (f"input[type='radio'][id='{decided['id']}']" if decided["id"]
                     else f"input[type='radio'][value='{decided['value']}']")
        try:
            await page.locator(radio_sel).first.click(force=True, timeout=4_000)
        except Exception:
            await page.locator(radio_sel).first.evaluate("el => el.click()")
        for sel in SUBMIT_SELECTORS:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).first.evaluate("el => el.click()")
                print(f"  submitted via {sel!r}")
                break
        try:
            await page.wait_for_selector(RESULTS_SELECTOR, timeout=30_000)
        except PlaywrightTimeout:
            print("  ⚠ no results container within 30s")
        await settle(page)

        body = await page.locator("body").inner_text()
        print(f"  title: {await page.title()!r}\n  url:   {page.url}")
        print(f"  total results the Decided list reports: {parse_total(body)}")
        soup = BeautifulSoup(await page.content(), "html.parser")
        items = soup.find_all("li", class_="searchresult")
        print(f"  result items on page 1: {len(items)}")
        links = []
        for li in items[:10]:
            meta = li.find(class_=re.compile(r"metaInfo", re.I))
            a = li.find("a", href=True)
            print(f"   - {meta.get_text(' ', strip=True) if meta else '(no metaInfo)'}")
            if a:
                links.append(urljoin(page.url, a["href"]))

        print("\n" + "=" * 60 + f"\nSTEP 3: the first {DETAIL_SAMPLES} application pages in full\n" + "=" * 60)
        for url in links[:DETAIL_SAMPLES]:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await settle(page, 1.0)
                pairs = detail_pairs(await page.content())
                print(f"\n  {url}\n  rows found: {len(pairs)}")
                for k, v in pairs[:30]:
                    flag = "  <<" if (k, v) in interesting(pairs) else ""
                    print(f"     {k[:38]:38} | {v[:70]}{flag}")
            except Exception as e:
                print(f"  ⚠ {url}: {type(e).__name__}: {str(e)[:120]}")
            await asyncio.sleep(3)

        await browser.close()
    print("\nProbe complete (nothing was written to the database).")


if __name__ == "__main__":
    asyncio.run(main())
