#!/usr/bin/env python3
"""
Idox "Decided list" survey (2026-10-07).

Medway's list pages said only "Decided", but its Decided list plus each
application's own page gave the outcome and decision date. Before building
the same thing for the other ~160 Idox councils we need to know, per council:
  - does the monthly list form offer a "Decided" option at all?
  - what do the Decided list's own status lines say? (some councils, such as
    Perth and Kinross and Mid Ulster, already put the outcome in the list)
  - if not, does the application's own page carry a Decision and a date,
    and what are those rows called?
This runs ONE quick check per sampled council: last month's Decided list,
and the first two application pages. It writes NOTHING to the database.
Needs idox_scraper.py, idox_councils.py and medway_decisions.py alongside it.
"""
import asyncio
import re
import time
from collections import Counter

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

import idox_scraper as scraper            # production browser settings
import medway_decisions as md             # its parsing helpers, reused
from idox_councils import IDOX_COUNCILS

SAMPLE = [
    "Leeds City Council", "Durham County Council", "London Borough of Lambeth", "Cornwall Council",
    "Exeter City Council", "Bedford Borough Council", "Peterborough City Council",
    "Stockport Metropolitan Borough Council", "Perth and Kinross Council", "Mid Ulster Council",
]
MONTH_INDEX = 1
DETAIL_SAMPLES = 2

RADIO_JS = """el => {
  let label = '';
  if (el.labels && el.labels.length) label = el.labels[0].textContent;
  else if (el.closest('label')) label = el.closest('label').textContent;
  else if (el.nextSibling && el.nextSibling.textContent) label = el.nextSibling.textContent;
  return {id: el.id, name: el.name, value: el.value, label: (label || '').trim().slice(0, 40)};
}"""


def entry_for(name):
    for e in IDOX_COUNCILS:
        if e[0] == name:
            return e[1], (e[3] if len(e) == 4 else "")
    return None


def choose_decided(radios):
    for r in radios:
        text = f"{r.get('label', '')} {r.get('value', '')}".lower()
        if "decid" in text and "valid" not in text:
            return r
    return None


def classify(info: dict) -> str:
    """Where this council sits, from what the survey found."""
    if info.get("error"):
        return "ERROR"
    if not info.get("decided_radio"):
        return "NO_DECIDED_OPTION"
    if not info.get("items"):
        return "LIST_MARKUP_DIFFERS" if info.get("raw_items") else "NO_RESULTS"
    statuses = info.get("list_statuses", {})
    total = sum(statuses.values()) or 1
    with_outcome = sum(n for s, n in statuses.items() if md.outcome_from_decision(s))
    if with_outcome / total >= 0.5:
        return "LIST_HAS_OUTCOME"
    for d in info.get("details", []):
        if any(re.search(r"decision|outcome", k) for k in d["fields"]):
            return "DETAIL_HAS_DECISION"
    return "NO_OUTCOME_FOUND"


MEANING = {
    "LIST_HAS_OUTCOME": "the Decided list already says approved/refused — no page reads needed",
    "DETAIL_HAS_DECISION": "list is generic, but the application page has the outcome — same as Medway",
    "NO_OUTCOME_FOUND": "neither the list nor the sampled pages show an outcome — look by hand",
    "NO_DECIDED_OPTION": "the monthly list form has no Decided choice",
    "NO_RESULTS": "the Decided list returned nothing",
    "LIST_MARKUP_DIFFERS": "results exist but the Medway parser can't read them — needs a look",
    "ERROR": "could not complete the check",
}


async def settle(page, extra=1.2):
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def survey_one(browser, name):
    info = {"name": name, "error": None, "radios": [], "decided_radio": None, "total": None,
            "items": 0, "raw_items": 0, "list_statuses": {}, "details": [], "secs": 0}
    got = entry_for(name)
    if got is None:
        info["error"] = "not in IDOX_COUNCILS"
        return info
    url, extra = got
    t0 = time.monotonic()
    context = await browser.new_context(**scraper.CONTEXT_OPTIONS)
    page = await context.new_page()
    try:
        try:
            await page.goto(f"{url}/search.do?action=simple&searchType=Application",
                            wait_until="domcontentloaded", timeout=25_000)
            await asyncio.sleep(1)
        except Exception:
            pass
        await page.goto(f"{url}/search.do?action=monthlyList&searchCriteria.monthYearIndex={MONTH_INDEX}"
                        f"&searchType=Application{extra}", wait_until="domcontentloaded", timeout=40_000)
        await settle(page)
        radios = []
        loc = page.locator("input[type='radio']")
        for i in range(await loc.count()):
            radios.append(await loc.nth(i).evaluate(RADIO_JS))
        info["radios"] = [(r["id"], r["value"], r["label"]) for r in radios if r["name"] in ("dateType", "")
                          or "date" in r["name"].lower()] or [(r["id"], r["value"], r["label"]) for r in radios]
        decided = choose_decided(radios)
        if not decided:
            return info
        info["decided_radio"] = (decided["id"], decided["value"])
        for sel in md.DROPDOWN_SELECTORS:
            if await page.locator(sel).count() > 0:
                try:
                    await page.locator(sel).first.select_option(index=MONTH_INDEX)
                except Exception:
                    pass
                break
        radio_sel = (f"input[type='radio'][id='{decided['id']}']" if decided["id"]
                     else f"input[type='radio'][value='{decided['value']}']")
        try:
            await page.locator(radio_sel).first.click(force=True, timeout=4_000)
        except Exception:
            await page.locator(radio_sel).first.evaluate("el => el.click()")
        for sel in md.SUBMIT_SELECTORS:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).first.evaluate("el => el.click()")
                break
        try:
            await page.wait_for_selector(md.RESULTS_SELECTOR, timeout=30_000)
        except PlaywrightTimeout:
            pass
        await settle(page)
        info["total"] = md.parse_total(await page.locator("body").inner_text())
        info["raw_items"] = await page.locator("li.searchresult").count()
        items = md.parse_list_items(await page.content(), page.url)
        info["items"] = len(items)
        info["list_statuses"] = dict(Counter(i["list_status"] for i in items))
        for it in items[:DETAIL_SAMPLES]:
            await asyncio.sleep(2)
            await page.goto(it["url"], wait_until="domcontentloaded", timeout=30_000)
            await settle(page, 0.8)
            fields = md.detail_fields(await page.content())
            info["details"].append({"ref": it["reference"],
                                    "fields": {k: v[:50] for k, v in fields.items()}})
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {str(e)[:140]}"
    finally:
        await context.close()
        info["secs"] = time.monotonic() - t0
    return info


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\nSurveying {len(SAMPLE)} councils, one at a time…\n")
        results = []
        for name in SAMPLE:
            info = await survey_one(browser, name)
            info["category"] = classify(info)
            results.append(info)
            print(f"{'=' * 60}\n{name}  [{info['category']}]  ({info['secs']:.0f}s)")
            if info["error"]:
                print(f"  error: {info['error']}")
            print(f"  date-type radios: {info['radios']}")
            print(f"  decided radio used: {info['decided_radio']}   list total: {info['total']}   "
                  f"items parsed: {info['items']} (raw {info['raw_items']})")
            print(f"  statuses on the Decided list's first page: {info['list_statuses']}")
            for d in info["details"]:
                keep = {k: v for k, v in d["fields"].items()
                        if re.search(r"decision|outcome|status|issued|decided", k)}
                print(f"  page {d['ref']}: all labels = {list(d['fields'])}")
                print(f"      decision-like rows: {keep}")
            await asyncio.sleep(3)
        await browser.close()

    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for cat, rows in sorted(((c, [r for r in results if r['category'] == c])
                             for c in {r['category'] for r in results}), key=lambda kv: -len(kv[1])):
        print(f"\n{cat} ({len(rows)}) — {MEANING[cat]}")
        for r in rows:
            print(f"   {r['name']}")
    texts = Counter(v for r in results for d in r["details"] for k, v in d["fields"].items()
                    if re.fullmatch(r"decision", k))
    if texts:
        print("\n'Decision' values seen on application pages, and how the Medway mapping reads them:")
        for text, n in texts.most_common():
            print(f"   {text!r:45} {n}  ->  {md.outcome_from_decision(text) or 'LEFT ALONE'}")
    print("\nSurvey complete (nothing was written to the database).")


if __name__ == "__main__":
    asyncio.run(main())
