#!/usr/bin/env python3
"""
PlanFind - Arun District Council scraper (OcellaWeb, headless Chromium).

Arun's planning search page embeds the OcellaWeb search from
  https://www1.arun.gov.uk/aplanning/OcellaWeb/planningSearch
which is the same product as Great Yarmouth, South Holland, Havering and Hillingdon (ocellaweb_scraper.py:
date range in DD-MM-YY into #receivedFrom / #receivedTo, results in a plain table
Reference | Location | Proposal | Received | Type | Status). Arun's own page says "for best results search
by one field only", and the platform's pagination has never been seen, so this scraper searches in 7-day
slices (each result set stays small), reads the table of each, and logs everything that could mean a second
page (result-count text, pager links) so a truncated set is visible rather than silent.

First runs are DRY_RUN: the log also prints the form's field ids (to find a decision-date search) and the text
of two detail pages (to learn the status wording).

Env: SUPABASE_URL, SUPABASE_KEY (not needed for DRY_RUN), ARUN_COUNCIL_ID (5), DAYS_BACK (30), SLICE_DAYS (7),
MAX_MINUTES (30), DRY_RUN (1 = write nothing).
"""
import asyncio
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

import ocellaweb_scraper as O  # tested helpers: _normalise_status, _extract_postcode, _parse_ocellaweb_date, geocode, ...

BASE = "https://www1.arun.gov.uk/aplanning"
SEARCH_URL = f"{BASE}/OcellaWeb/planningSearch"
SUPABASE_URL, SUPABASE_KEY = O.SUPABASE_URL, O.SUPABASE_KEY
COUNCIL_ID = int(os.environ.get("ARUN_COUNCIL_ID", "5") or 5)
DAYS_BACK = int(os.environ.get("DAYS_BACK", "30"))
SLICE_DAYS = int(os.environ.get("SLICE_DAYS", "7"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "30"))
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
SOURCE = "arun_scraper"
START = time.monotonic()


def minutes() -> float:
    return (time.monotonic() - START) / 60


def status_of(raw: str) -> str:
    s = (raw or "").lower()
    if "withdraw" in s:
        return "withdrawn"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permitted")):
        return "refused"
    if "undecided" in s or not s.strip():
        return "pending"
    if any(x in s for x in ("approv", "grant", "permit", "allow", "no objection", "consent", "not required", "certif")):
        return "approved"
    if any(x in s for x in ("invalid", "returned", "closed", "cancel", "lapsed", "disposed", "no further action")):
        return "closed"
    return "pending"


_STATUS_SEEN: Counter = Counter()


def parse_table(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table") if len(t.find_all("tr")) > 1 and t.find("th")), None)
    if not table:
        return []
    rows = table.find_all("tr")
    head = [th.get_text(" ", strip=True).lower() for th in rows[0].find_all("th")]

    def col(*kw):
        for i, h in enumerate(head):
            if any(k in h for k in kw):
                return i
        return None
    i_ref, i_loc, i_pro, i_rec, i_type, i_stat = (col("reference"), col("location", "address"), col("proposal", "description"),
                                                  col("received"), col("type"), col("status", "decision"))
    if i_ref is None:
        print(f"    ⚠ table without a Reference column; headers {head}")
        return []
    out = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if len(tds) <= i_ref:
            continue
        ref = tds[i_ref].get_text(strip=True)
        if not ref:
            continue
        a = tds[i_ref].find("a")
        href = a["href"] if a and a.get("href") else ""
        url = href if href.startswith("http") else (f"{BASE}/OcellaWeb/{href.lstrip('/')}" if href
                                                     else f"{BASE}/OcellaWeb/planningDetails?reference={ref}&from=planningSearch")
        g = lambda i: tds[i].get_text(" ", strip=True) if i is not None and i < len(tds) else ""
        address = g(i_loc)
        raw = g(i_stat)
        _STATUS_SEEN[raw] += 1
        out.append({"reference": ref, "address": address, "postcode": O._extract_postcode(address),
                    "description": g(i_pro), "application_type": g(i_type) or None,
                    "submitted_date": O._parse_ocellaweb_date(g(i_rec)), "raw_status": raw,
                    "status": status_of(raw), "council_url": url})
    return out


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass


_FORM_DUMPED = False


async def search_slice(ctx, a: date, b: date) -> tuple:
    global _FORM_DUMPED
    page = await ctx.new_page()
    try:
        for attempt in (1, 2, 3):
            try:
                await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45000)
                await settle(page)
                if not _FORM_DUMPED:
                    _FORM_DUMPED = True
                    ids = await page.eval_on_selector_all("input, select, textarea", "els => els.map(e => e.id + ':' + e.type + ':' + (e.name||''))")
                    print(f"    ℹ form fields (id:type:name): {[i for i in ids if i.strip(':')][:40]}")
                await page.fill("#receivedFrom", a.strftime("%d-%m-%y"), timeout=8000)
                await page.fill("#receivedTo", b.strftime("%d-%m-%y"), timeout=8000)
                form = page.locator("form").filter(has=page.locator("#receivedFrom"))
                btn = form.locator("button:has-text('Search')")
                if await btn.count() == 0:
                    btn = form.locator("input[type='submit'][value='Search']")
                await btn.first.click(timeout=8000)
                await settle(page)
                await asyncio.sleep(1)
                break
            except Exception as e:
                print(f"    ⚠ slice {a:%d/%m}-{b:%d/%m} attempt {attempt} failed: {type(e).__name__}")
                if attempt == 3:
                    raise
                await asyncio.sleep(5 * attempt)
        html = await page.content()
        text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
        hints = re.findall(r"(?:\d[\d,]*\s+(?:results?|records?|applications?|matches)[^.]{0,40}|page\s+\d+\s+of\s+\d+|showing\s+[^.]{0,50}|no (?:results|applications)[^.]{0,40})", text, re.I)[:3]
        pager = await page.eval_on_selector_all(
            "a", "els => els.map(e => (e.innerText||'').trim() + '>' + (e.getAttribute('href')||'')).filter(s => /next|prev|page|offset|start=|\\\\b\\\\d+>/i.test(s))")
        return parse_table(html), hints, pager[:8], text
    finally:
        await page.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Arun scraper ({SEARCH_URL})")
    print(f"Days back {DAYS_BACK}   slice {SLICE_DAYS}d   DRY_RUN {DRY_RUN}   max {MAX_MINUTES} min   council_id {COUNCIL_ID}")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and not (SUPABASE_URL and SUPABASE_KEY):
        print("ERROR: set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    today = date.today()
    d_from = today - timedelta(days=DAYS_BACK)
    apps: dict = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=O.BROWSER_ARGS)
        ctx = await browser.new_context(**O.CONTEXT_OPTIONS)
        start = d_from
        while start <= today:
            if minutes() > MAX_MINUTES:
                print(f"    ⚠ MAX_MINUTES reached at {start:%d/%m/%Y}")
                break
            end = min(start + timedelta(days=SLICE_DAYS - 1), today)
            rows, hints, pager, text = await search_slice(ctx, start, end)
            for r in rows:
                apps[r["reference"]] = r
            print(f"    ℹ received {start:%d/%m/%Y}-{end:%d/%m/%Y}: {len(rows)} rows, {len(apps)} total; count text {hints}; pager links {pager}")
            if not rows:
                print(f"      page text: {text[:300]}")
            if len(rows) >= 50:
                print(f"      ⚠ {len(rows)} rows in one slice - may be truncated")
            start = end + timedelta(days=1)
            await asyncio.sleep(1)

        if DRY_RUN and apps:
            print("\n    --- two detail pages (to learn the status wording) ---")
            for r in list(apps.values())[:2]:
                p = await ctx.new_page()
                try:
                    await p.goto(r["council_url"], wait_until="domcontentloaded", timeout=30000)
                    await settle(p)
                    print(f"    {r['reference']} {r['council_url']}\n      ", re.sub(r"\s+", " ", await p.locator("body").inner_text())[:1200])
                except Exception as e:
                    print(f"    detail failed: {type(e).__name__}")
                finally:
                    await p.close()
        await browser.close()

    rows = list(apps.values())
    print(f"\n    ℹ {len(rows)} applications; by status {dict(Counter(r['status'] for r in rows))}")
    print(f"    ℹ raw status text -> count: {dict(_STATUS_SEEN.most_common(15))}")
    print(f"    ℹ application types: {dict(Counter(r['application_type'] for r in rows).most_common(10))}")
    print(f"    ℹ missing postcode {sum(1 for r in rows if not r['postcode'])}; missing received date {sum(1 for r in rows if not r['submitted_date'])}")
    if DRY_RUN:
        print("\n    --- 6 sample rows ---")
        for r in rows[:6]:
            print("   ", {k: v for k, v in r.items() if k != 'raw_status'})
        print("\nDRY_RUN: nothing written.")
        return
    if not rows:
        print("    ✗ nothing found - not saving")
        sys.exit(1)
    coords = await O.geocode([r["postcode"] for r in rows if r["postcode"]])
    records, fb = [], 0
    for r in rows:
        lat, lng = coords.get((r["postcode"] or "").upper().replace(" ", ""), (None, None))
        fb += lat is None
        records.append({"council_id": COUNCIL_ID, "reference": r["reference"], "address": r["address"] or None,
                        "postcode": r["postcode"], "description": r["description"] or None,
                        "application_type": r["application_type"], "status": r["status"],
                        "submitted_date": r["submitted_date"], "council_url": r["council_url"],
                        "lat": lat, "lng": lng, "source": SOURCE})
    print(f"    Geocoded {len(rows) - fb} of {len(rows)}; centroid fallback for {fb}")
    ok = True
    for i in range(0, len(records), 50):
        ok = await O._supa_upsert(records[i:i + 50]) and ok
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        await O._supa_patch_council(COUNCIL_ID, {"coverage_source": SOURCE, "last_scraped_at": now, "last_saved_at": now})
        print(f"    ✓ Saved {len(records)}")
    else:
        print("    ✗ save failed")
        sys.exit(1)
    print(f"\nFinished in {minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
