#!/usr/bin/env python3
"""
Medway decisions (2026-10-06).

WHY: Medway's Idox list pages only say "Status: Decided" (or "Registered"),
never the outcome, so the normal Idox scrape labels all 887 backfilled
applications 'pending'. Confirmed by medway_decided_probe.py:
  - the monthly list form has a "Decided in this month" radio
    (input#dateDecided, name=dateType, value=DC_Decided)
  - last September's Decided list held 139 applications
  - each application's own page (summary tab) carries
      Decision             e.g. "Approval", "Approval with Conditions", "Refusal"
      Decision Issued Date e.g. "Wed 30 Sep 2026"

WHAT IT DOES, for the current and previous MONTHS_BACK months:
  1. runs the Decided list and collects every application on it
  2. reads Medway's rows from the database and keeps only applications that
     are still 'pending' (already-decided ones are never fetched again)
  3. opens each one's page once, reads the outcome and date
  4. UPDATES that row's status and decision_date (a PATCH, never an insert,
     so it cannot create half-empty rows). Applications not in the database
     (received before the backfill window) are skipped.
Safe to re-run: it only ever touches pending rows. Newest month first, so a
run that hits its time/page budget has done the most useful ones.

IMPORTANT — SCHEDULING: the nightly Idox scrape re-saves the last 14 days of
Medway applications from the list pages, which say 'Decided', and that
overwrites a decision made here back to 'pending'. Run this job AFTER the
Idox batch that contains Medway so the night ends with the right answer.

Env: MONTHS_BACK (default 2), START_MONTH (default 0 = this month; set it to
skip months that are already fully done), MAX_DETAIL (default 200 application
pages per run), MAX_MINUTES (default 30), DETAIL_PACE_SECONDS (default 2.0),
DRY_RUN=1 to print what would be written without writing.
"""
import asyncio
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

import idox_scraper as scraper                       # production helpers only
from idox_councils import IDOX_COUNCILS

COUNCIL_NAME = "Medway Council"
MONTHS_BACK = int(os.environ.get("MONTHS_BACK", "2"))
START_MONTH = int(os.environ.get("START_MONTH", "0"))   # skip months already finished
MAX_DETAIL = int(os.environ.get("MAX_DETAIL", "200"))
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "30"))
PACE = float(os.environ.get("DETAIL_PACE_SECONDS", "2.0"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
START = time.monotonic()

DECIDED_STATES = {"approved", "refused", "withdrawn"}
DROPDOWN_SELECTORS = ["select[id='month']", "select[name='month']",
                      "select[id='searchCriteria.monthYearIndex']", "select[name*='monthYear']"]
SUBMIT_SELECTORS = ["#monthlyListForm input[type='submit']", "#monthlyListForm input.button",
                    "form input[type='submit']", "form button[type='submit']", "input.button"]
RESULTS_SELECTOR = "ul.searchresults, #searchResultsContainer, .searchresults, .no-results, #searchResultsForm"
TOTAL_RE = re.compile(r"Showing\s+\d+\s*[-–]\s*\d+\s+of\s+(\d+)", re.I)
REF_RE = re.compile(r"Ref\.?\s*No\.?:?\s*([^\s|]+)", re.I)
STATUS_RE = re.compile(r"Status:?\s*([^|]+)$", re.I)


def minutes() -> float:
    return (time.monotonic() - START) / 60


# --------------------------------------------------------------- parsing

def parse_total(body):
    m = TOTAL_RE.search(body or "")
    return int(m.group(1)) if m else None


def parse_list_items(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.find_all("li", class_="searchresult"):
        meta = li.find(class_=re.compile(r"metaInfo", re.I))
        link = li.find("a", href=True)
        if not meta or not link:
            continue
        text = " ".join(meta.get_text(" ", strip=True).split())
        ref = REF_RE.search(text)
        status = STATUS_RE.search(text)
        if ref:
            out.append({"reference": ref.group(1), "url": urljoin(page_url, link["href"]),
                        "list_status": status.group(1).strip() if status else ""})
    return out


def detail_fields(html: str) -> dict:
    """lower-cased label -> value from an Idox application summary table."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="simpleDetailsTable") or soup.find("table")
    fields = {}
    if table:
        for tr in table.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                fields[th.get_text(" ", strip=True).lower()] = td.get_text(" ", strip=True)
    return fields


def outcome_from_decision(text):
    """approved / refused / withdrawn, or None when the text isn't a clear
    outcome (those are left untouched and listed in the end-of-run inventory)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if "withdraw" in t:
        return "withdrawn"
    if "refus" in t or "reject" in t or "unlawful" in t or "not lawful" in t:
        return "refused"
    if "split" in t or "prior approval required" in t:
        return None                    # not a plain outcome
    if "discharge" in t:
        # "Discharge of Conditions" = the conditions were satisfied (same call as
        # Bath's "Condition Discharged"); a negative or partial discharge is unclear.
        return None if ("not" in t or "part" in t) else "approved"
    if any(k in t for k in ("approv", "grant", "permit", "no objection", "not required",
                            "lawful", "with conditions", "deemed")):
        return "approved"
    return None


def parse_issue_date(s):
    try:
        return datetime.strptime((s or "").strip(), "%a %d %b %Y").date().isoformat()
    except ValueError:
        return None


def plan_work(decided: list[dict], existing: dict) -> tuple[list[dict], dict]:
    """Which decided applications need their page read. Only references we
    already hold, and only while they are not yet in a decided state."""
    stats = {"on_lists": len(decided), "already_decided": 0, "not_in_db": 0, "to_fetch": 0}
    todo = []
    for d in decided:
        status = existing.get(d["reference"])
        if status is None:
            stats["not_in_db"] += 1
        elif status in DECIDED_STATES:
            stats["already_decided"] += 1
        else:
            todo.append(d)
    stats["to_fetch"] = len(todo)
    return todo, stats


# ------------------------------------------------------------------- I/O

async def settle(page, extra=1.2):
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def collect_decided(context, base, month_index) -> list[dict]:
    page = await context.new_page()
    items: list[dict] = []
    try:
        try:
            await page.goto(f"{base}/search.do?action=simple&searchType=Application",
                            wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(1)
        except Exception:
            pass
        await page.goto(f"{base}/search.do?action=monthlyList"
                        f"&searchCriteria.monthYearIndex={month_index}&searchType=Application",
                        wait_until="domcontentloaded", timeout=45_000)
        await settle(page)
        for sel in DROPDOWN_SELECTORS:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).first.select_option(index=month_index)
                break
        radio = page.locator("input#dateDecided")
        if await radio.count() == 0:
            radio = page.locator("input[value='DC_Decided']")
        if await radio.count() == 0:
            print(f"    [month {month_index}] ⚠ no 'Decided' radio found — skipping this month")
            return []
        try:
            await radio.first.click(force=True, timeout=4_000)
        except Exception:
            await radio.first.evaluate("el => el.click()")
        for sel in SUBMIT_SELECTORS:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).first.evaluate("el => el.click()")
                break
        try:
            await page.wait_for_selector(RESULTS_SELECTOR, timeout=30_000)
        except PlaywrightTimeout:
            print(f"    [month {month_index}] ⚠ no results container within 30s")
            return []
        await settle(page)

        total = parse_total(await page.locator("body").inner_text())
        pages = math.ceil(total / 10) if total else 1
        first = parse_list_items(await page.content(), page.url)
        items.extend(first)
        previous = {i["reference"] for i in first}
        for n in range(2, pages + 1):
            await asyncio.sleep(1.5)
            await page.goto(f"{base}/pagedSearchResults.do?action=page&searchCriteria.page={n}",
                            wait_until="domcontentloaded", timeout=30_000)
            await settle(page, 1.0)
            if "too many requests" in (await page.title()).lower():
                print(f"    [month {month_index}] ⚠ rate-limited at page {n} — stopping this month")
                break
            got = parse_list_items(await page.content(), page.url)
            refs = {i["reference"] for i in got}
            if not got or refs == previous:
                print(f"    [month {month_index}] page {n} empty/identical — stopping")
                break
            previous = refs
            items.extend(got)
        print(f"    [month {month_index}] Decided list: {total} reported, {len(items)} collected over {pages} page(s)")
    except Exception as e:
        print(f"    [month {month_index}] ⚠ {type(e).__name__}: {str(e)[:150]}")
    finally:
        await page.close()
    return items


async def fetch_existing(cid) -> dict:
    existing, size = {}, 1000
    for page in range(10):
        rows = await scraper._supa_get("planning_applications", select="reference,status",
                                       council_id=f"eq.{cid}", order="id.asc",
                                       limit=str(size), offset=str(page * size))
        for r in rows:
            existing[r["reference"]] = r["status"]
        if len(rows) < size:
            break
    return existing


async def patch_application(client, cid, reference, fields) -> bool:
    r = await client.patch(f"{scraper.SUPABASE_URL}/rest/v1/planning_applications",
                           params={"council_id": f"eq.{cid}", "reference": f"eq.{reference}"},
                           json=fields, headers={**scraper._h(), "Prefer": "return=minimal"})
    if r.status_code not in (200, 204):
        print(f"    ✗ PATCH {reference}: HTTP {r.status_code} {r.text[:150]}")
        return False
    return True


async def main():
    if not scraper.SUPABASE_URL or not scraper.SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    entry = next((e for e in IDOX_COUNCILS if e[0] == COUNCIL_NAME), None)
    if entry is None:
        print(f"ERROR: {COUNCIL_NAME} is not in IDOX_COUNCILS — deploy the updated idox_councils.py first.")
        sys.exit(1)
    base = entry[1]
    rows = await scraper._supa_get("councils", select="id,name", name=f"eq.{COUNCIL_NAME}")
    if len(rows) != 1 or not isinstance(rows[0].get("id"), int):
        print(f"ERROR: expected exactly one councils row named {COUNCIL_NAME!r}, found {len(rows)}")
        sys.exit(1)
    cid = rows[0]["id"]
    print(f"{COUNCIL_NAME}: council_id={cid}  portal={base}\n"
          f"months={START_MONTH}..{MONTHS_BACK - 1}  max pages={MAX_DETAIL}  budget={MAX_MINUTES} min  "
          f"{'DRY RUN — nothing will be written' if DRY_RUN else 'LIVE'}\n")

    existing = await fetch_existing(cid)
    pending_before = sum(1 for s in existing.values() if s == "pending")
    print(f"Database holds {len(existing)} Medway applications, {pending_before} pending\n")

    stats_total = Counter()
    inventory: Counter = Counter()
    written = Counter()
    seen_refs: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=scraper.BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**scraper.CONTEXT_OPTIONS)
        client = httpx.AsyncClient(timeout=30)

        fetched = 0
        for month_index in range(START_MONTH, MONTHS_BACK):
            if minutes() >= MAX_MINUTES - 3 or fetched >= MAX_DETAIL:
                print("Budget reached — stopping before month", month_index)
                break
            decided = [d for d in await collect_decided(context, base, month_index)
                       if d["reference"] not in seen_refs]
            seen_refs.update(d["reference"] for d in decided)
            todo, stats = plan_work(decided, existing)
            stats_total.update(stats)
            print(f"    month {month_index}: {stats}")

            page = await context.new_page()
            for d in todo:
                if minutes() >= MAX_MINUTES - 1 or fetched >= MAX_DETAIL:
                    print("    budget reached mid-month — stopping")
                    break
                fetched += 1
                try:
                    await page.goto(d["url"], wait_until="domcontentloaded", timeout=30_000)
                    await settle(page, 0.8)
                    if "too many requests" in (await page.title()).lower():
                        print("    ⚠ rate-limited on an application page — stopping")
                        fetched = MAX_DETAIL
                        break
                    fields = detail_fields(await page.content())
                except Exception as e:
                    print(f"    ⚠ {d['reference']}: {type(e).__name__}: {str(e)[:100]}")
                    written["page_failed"] += 1
                    await asyncio.sleep(PACE)
                    continue
                decision = fields.get("decision", "")
                inventory[decision or "(no decision row)"] += 1
                status = outcome_from_decision(decision)
                issued = parse_issue_date(fields.get("decision issued date"))
                if status is None:
                    written["unmapped"] += 1
                else:
                    update = {"status": status}
                    if issued:
                        update["decision_date"] = issued
                    print(f"    {d['reference']:14} {decision!r:28} -> {status:9} {issued or ''}")
                    if DRY_RUN or await patch_application(client, cid, d["reference"], update):
                        written[status] += 1
                await asyncio.sleep(PACE)
            await page.close()

        await client.aclose()
        await browser.close()

    print(f"\n{'=' * 60}\nSUMMARY ({'dry run' if DRY_RUN else 'live'}, {minutes():.1f} min)\n{'=' * 60}")
    print(f"  Decided-list applications seen: {stats_total['on_lists']}")
    print(f"    already decided in the database: {stats_total['already_decided']}")
    print(f"    not in the database (skipped):   {stats_total['not_in_db']}")
    print(f"    needing their page read:         {stats_total['to_fetch']}  (read this run: {fetched})")
    print(f"  Updated: approved={written['approved']}  refused={written['refused']}  "
          f"withdrawn={written['withdrawn']}   left alone (unclear outcome)={written['unmapped']}   "
          f"page failures={written['page_failed']}")
    print("  Raw 'Decision' text seen on application pages:")
    for text, n in inventory.most_common():
        print(f"     {text!r:40} {n:4}  ->  {outcome_from_decision(text) or 'LEFT ALONE'}")
    remaining = stats_total["to_fetch"] - fetched
    if remaining > 0:
        print(f"\n  {remaining} applications still need their page read — run again to continue.")


if __name__ == "__main__":
    asyncio.run(main())
