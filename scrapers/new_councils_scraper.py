#!/usr/bin/env python3
"""
PlanFind - City of Lincoln (576), Barnsley (577), Rotherham (578).  Written 2026-11-24 from the recon log.

  lincoln    Idox weekly list (same markup as the Boston/East Lindsey portal). Validated-this-week and Decided-this-week
             lists for the last few weeks; decisions read from each decided application's Summary tab.
  barnsley   Planning Explorer advanced search, Received-date windows (7 days). The results table shows reference,
             description, address, 'Valid From' (used as the submitted date), decision and status.
  rotherham  classic ASP weekly list ('Select Weekly List': Most Recent, 1 Week Ago ...). The list has no per-application
             date, so each application's detail page is read for its received date; if that fails the week-ending date is used.
             Weekly lists only carry new applications, so everything is saved 'pending' until a decision source is added.

Env: SUPABASE_URL, SUPABASE_KEY, ONLY (lincoln,barnsley,rotherham; blank = all), DRY_RUN (1 = write nothing; default 1),
     DAYS_BACK (21), MAX_MINUTES (40), DETAIL_DELAY (1.0).
"""
import asyncio
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

import east_lindsey_scraper as E
import lincs_shared_scraper as L   # parse_page / read_decision / decision_to_status are reused for Lincoln's Idox pages

IDS = {"lincoln": int(os.environ.get("LINCOLN_COUNCIL_ID", "576")),
       "barnsley": int(os.environ.get("BARNSLEY_COUNCIL_ID", "577")),
       "rotherham": int(os.environ.get("ROTHERHAM_COUNCIL_ID", "578"))}
ONLY = [x.strip().lower() for x in os.environ.get("ONLY", "").split(",") if x.strip()]
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
DAYS_BACK = int(os.environ.get("DAYS_BACK", "21"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "40"))
DETAIL_DELAY = float(os.environ.get("DETAIL_DELAY", "1.0"))
START = time.monotonic()
LINCOLN_BASE = "https://planning.lincoln.gov.uk/online-applications"
BARNSLEY_BASE = "https://planningexplorer.barnsley.gov.uk"
ROTHERHAM_BASE = "https://planning.rotherham.gov.uk"


def minutes() -> float:
    return (time.monotonic() - START) / 60


def short(t, n=300):
    return re.sub(r"\s+", " ", t or "")[:n]


def parse_long_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%a %d %b %Y", "%A %d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


# ----------------------------------------------------------------------------- Lincoln
async def lincoln(browser) -> list:
    L.BASE = LINCOLN_BASE                      # parse_page builds detail links from this
    ctx = await browser.new_context(**E.CONTEXT_OPTIONS)
    page = await ctx.new_page()
    weeks_needed = max(1, (DAYS_BACK + 6) // 7)
    found: dict = {}
    decided: dict = {}

    async def read_pages(kind: str, bucket: dict):
        n_pages = 0
        while True:
            n_pages += 1
            html = await page.content()
            for r in L.parse_page(html):
                bucket[r["reference"]] = r
            nxt = page.locator("a.next")
            if n_pages > 40 or not await nxt.count():
                break
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                    await nxt.first.click()
            except Exception:
                break

    for kind, radio, bucket in (("validated", "dateValidated", found), ("decided", "dateDecided", decided)):
        for w in range(weeks_needed):
            if minutes() > MAX_MINUTES:
                print("    ⚠ MAX_MINUTES reached"); break
            try:
                await page.goto(f"{LINCOLN_BASE}/search.do?action=weeklyList", wait_until="domcontentloaded", timeout=60000)
                opts = await page.locator("select#week option").evaluate_all("els => els.map(e => [e.value, e.textContent.trim()])")
                if w >= len(opts):
                    break
                await page.select_option("select#week", index=w)
                await page.check(f"input#{radio}")
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
                    await page.locator("input[type=submit]").first.click()
                before = len(bucket)
                await read_pages(kind, bucket)
                print(f"    ℹ lincoln {kind} week {opts[w][1]}: +{len(bucket) - before} (total {len(bucket)})")
            except Exception as e:
                print(f"    ✗ lincoln {kind} week {w}: {type(e).__name__}: {str(e)[:100]}")
            await asyncio.sleep(1)

    dec_info = {}
    for n, ref in enumerate(decided, 1):
        if minutes() > MAX_MINUTES:
            print("    ⚠ MAX_MINUTES reached while reading decisions"); break
        try:
            dec_info[ref] = await L.read_decision(page, decided[ref]["council_url"])
            if n <= 5:
                print(f"    ℹ sample decision {ref}: {dec_info[ref][0]!r} on {dec_info[ref][1]}")
        except Exception as e:
            print(f"    ⚠ decision read failed for {ref}: {type(e).__name__}")
        await asyncio.sleep(DETAIL_DELAY)
    await ctx.close()

    out = []
    for ref in dict.fromkeys(list(decided) + list(found)):
        row = found.get(ref) or decided[ref]
        status = E._normalise_status(row["raw_status"])
        dtext, ddate = dec_info.get(ref, ("", None))
        dstat = L.decision_to_status(dtext) if (ref in decided and dtext) else None
        if ref in decided and dstat:
            status = dstat
        elif ref in decided and status == "pending":
            status = "closed"
        out.append({"reference": ref, "address": row["address"] or None, "postcode": row["postcode"],
                    "description": row["description"] or None, "application_type": "Planning", "status": status,
                    "submitted_date": row["submitted_date"].isoformat() if row["submitted_date"] else None,
                    "decision_date": ddate.isoformat() if (ddate and status != "pending") else None,
                    "council_url": row["council_url"]})
    return out


# ----------------------------------------------------------------------------- Barnsley
def parse_barnsley(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        ref = tds[0].get_text(" ", strip=True)
        if not re.match(r"^\d{4}/\d{3,5}", ref):
            continue
        a = tds[0].find("a")
        href = a.get("href") if a else None
        address = tds[2].get_text(" ", strip=True)
        valid = parse_long_date(tds[3].get_text(" ", strip=True))
        decision = tds[4].get_text(" ", strip=True) if len(tds) > 4 else ""
        status_txt = tds[5].get_text(" ", strip=True) if len(tds) > 5 else ""
        out.append({"reference": ref, "description": tds[1].get_text(" ", strip=True), "address": address,
                    "postcode": E._extract_postcode(address), "submitted_date": valid, "decision": decision,
                    "raw_status": status_txt, "council_url": urljoin(BARNSLEY_BASE + "/", href) if href else None})
    return out


async def barnsley(browser) -> list:
    ctx = await browser.new_context(**E.CONTEXT_OPTIONS)
    page = await ctx.new_page()
    today = date.today()
    got: dict = {}
    start = today - timedelta(days=DAYS_BACK)
    while start <= today:
        if minutes() > MAX_MINUTES:
            print("    ⚠ MAX_MINUTES reached"); break
        end = min(start + timedelta(days=6), today)
        try:
            await page.goto(f"{BARNSLEY_BASE}/Home/AdvanceSearch", wait_until="domcontentloaded", timeout=60000)
            try:                                   # the date fields sit in a collapsed "Search by dates" panel
                await page.get_by_text("Search by dates").first.click(timeout=10000)
                await page.wait_for_timeout(800)
            except Exception:
                pass
            for sel, d in (("dateReceivedFrom", start), ("dateReceivedTo", end)):
                val = d.strftime("%d/%m/%Y")
                try:                                # type it like a person: the box has a date mask and ignores a plain fill
                    box = page.locator(f"#{sel}")
                    await box.click(timeout=8000)
                    await box.press("Control+a")
                    await box.press_sequentially(val, delay=40)
                    await box.press("Tab")
                except Exception:                   # still hidden: set it directly
                    await page.evaluate("([s, v]) => { const e = document.getElementById(s); e.value = v; "
                                        "e.dispatchEvent(new Event('input', {bubbles: true})); "
                                        "e.dispatchEvent(new Event('change', {bubbles: true})); }", [sel, val])
            await page.get_by_role("button", name=re.compile("^search$", re.I)).first.click()   # same click the recon used
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2500)
            pages = 0
            while True:
                pages += 1
                rows = parse_barnsley(await page.content())
                for r in rows:
                    got[r["reference"]] = r
                nxt = page.locator("a:has-text('Next'), a[rel=next], li.next a, a:has-text('>')")
                if pages > 30 or not await nxt.count():
                    break
                try:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                        await nxt.first.click()
                except Exception:
                    break
            body = short(await page.inner_text("body"), 1500)
            hdr = re.search(r"Results for Application Search\s*:\s*(\d+)", body)
            if not hdr or not rows:
                vals = await page.evaluate("() => ['dateReceivedFrom','dateReceivedTo'].map(i => (document.getElementById(i)||{}).value)")
                tinfo = await page.evaluate("() => [...document.querySelectorAll('table')].map(t => t.querySelectorAll('tr').length + 'tr/' + (t.querySelector('tr:nth-child(2)')||{children:[]}).children.length + 'c')")
                print(f"      ⚠ barnsley diagnostic: url {page.url}; date fields now {vals}; tables {tinfo}; body: {body[:500]}")
            print(f"    ℹ barnsley {start:%d/%m}-{end:%d/%m}: page claims {hdr.group(1) if hdr else '?'}, read {pages} page(s), total {len(got)}")
        except Exception as e:
            print(f"    ✗ barnsley {start:%d/%m}-{end:%d/%m}: {type(e).__name__}: {str(e)[:100]}")
        start = end + timedelta(days=1)
        await asyncio.sleep(1)
    await ctx.close()
    out = []
    for ref, r in got.items():
        status = E._normalise_status(r["raw_status"])
        ds = L.decision_to_status(r["decision"]) if r["decision"] else None
        if ds:
            status = ds
        out.append({"reference": ref, "address": r["address"] or None, "postcode": r["postcode"],
                    "description": r["description"] or None, "application_type": "Planning", "status": status,
                    "submitted_date": r["submitted_date"].isoformat() if r["submitted_date"] else None,
                    "decision_date": None, "council_url": r["council_url"]})
    return out


# ----------------------------------------------------------------------------- Rotherham
def parse_rotherham(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for lab in soup.find_all(string=re.compile(r"^\s*App\. No\.:?\s*$")):
        cell = lab.find_parent("td")
        if not cell:
            continue
        tbl = cell.find_parent("table")
        if not tbl:
            continue
        def val(label):
            c = tbl.find(string=re.compile(r"^\s*" + re.escape(label) + r"\s*:?\s*$"))
            n = c.find_parent("td").find_next_sibling("td") if c and c.find_parent("td") else None
            return n.get_text(" ", strip=True) if n else ""
        ref = val("App. No.")
        if not ref:
            continue
        addr = re.sub(r"\s+\d{4}$", "", val("Site Address")).strip()    # list appends a 4-digit code (e.g. 0186, 9999)
        a = tbl.find("a", href=re.compile(r"detail\.asp"))
        out.append({"reference": ref, "address": addr, "description": val("Description"),
                    "postcode": E._extract_postcode(addr), "detail": urljoin(ROTHERHAM_BASE + "/", a["href"]) if a else None})
    return out


async def rotherham(browser) -> list:
    ctx = await browser.new_context(**E.CONTEXT_OPTIONS)
    page = await ctx.new_page()
    weeks_needed = max(1, (DAYS_BACK + 6) // 7)
    rows: dict = {}
    week_end: dict = {}
    for w in range(weeks_needed):
        if minutes() > MAX_MINUTES:
            print("    ⚠ MAX_MINUTES reached"); break
        try:
            await page.goto(f"{ROTHERHAM_BASE}/weeklylistapp.asp", wait_until="domcontentloaded", timeout=60000)
            sel = page.locator("select[name='lbxWeeklyListToShow']")
            vals = await sel.evaluate("e => [...e.options].map(o => o.value)")
            if w >= len(vals):
                break
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
                await sel.select_option(value=vals[w])
                await page.locator("input[type=submit][value='Go']").first.click()
            txt = short(await page.inner_text("body"), 4000)
            m = re.search(r"week ending (\d{1,2} \w+ \d{4})", txt)
            wend = parse_long_date(m.group(1)) if m else None
            got = parse_rotherham(await page.content())
            for r in got:
                rows[r["reference"]] = r
                week_end[r["reference"]] = wend
            claimed = re.search(r"registered during this period were:\s*(\d+)", txt)
            print(f"    ℹ rotherham week ending {wend}: read {len(got)}"
                  f"{' (page says ' + claimed.group(1) + ')' if claimed else ''}, total {len(rows)}")
        except Exception as e:
            print(f"    ✗ rotherham week {w}: {type(e).__name__}: {str(e)[:100]}")
        await asyncio.sleep(1)

    shown = 0
    out = []
    for n, (ref, r) in enumerate(rows.items(), 1):
        d = None
        if r["detail"] and minutes() < MAX_MINUTES:
            try:
                await page.goto(r["detail"], wait_until="domcontentloaded", timeout=45000)
                t = short(await page.inner_text("body"), 6000)
                m = re.search(r"(?:Date\s+(?:Application\s+)?(?:Received|Registered|Valid(?:ated)?)|Received(?:\s+Date)?)\s*:?\s*(\d{1,2}[ /]\w+[ /]\d{4})", t, re.I)
                d = parse_long_date(m.group(1)) if m else None
                if shown < 4:
                    shown += 1
                    print(f"    ℹ rotherham detail {ref}: received {d}   text: {short(t, 160)}")
            except Exception as e:
                print(f"    ⚠ rotherham detail failed for {ref}: {type(e).__name__}")
            await asyncio.sleep(DETAIL_DELAY)
        d = d or week_end.get(ref)
        out.append({"reference": ref, "address": r["address"] or None, "postcode": r["postcode"],
                    "description": r["description"] or None, "application_type": "Planning", "status": "pending",
                    "submitted_date": d.isoformat() if d else None, "decision_date": None, "council_url": r["detail"]})
    await ctx.close()
    return out


# ----------------------------------------------------------------------------- save
async def save(who: str, recs: list):
    cid = IDS[who]
    if not recs:
        print(f"[{who}] nothing to save")
        if not DRY_RUN:
            await E._supa_patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        return
    print(f"[{who}] {len(recs)} applications, by status {dict(Counter(r['status'] for r in recs))}; "
          f"with submitted date {sum(1 for r in recs if r['submitted_date'])}")
    for r in recs[:6]:
        print("   ", r["reference"], "|", r["address"], "|", r["status"], "|", r["submitted_date"], "|", r["decision_date"])
    if DRY_RUN:
        return
    coords = await E.geocode([r["postcode"] for r in recs if r["postcode"]])
    fallback = 0
    for r in recs:
        key = (r["postcode"] or "").upper().replace(" ", "")
        r["lat"], r["lng"] = coords.get(key, (None, None))
        fallback += r["lat"] is None
        r["council_id"] = cid
        r["source"] = f"new_councils_scraper"
        if not r["submitted_date"]:
            del r["submitted_date"]            # never overwrite a known date with an unknown one
        if not r["decision_date"]:
            del r["decision_date"]
    print(f"[{who}] geocoded {len(recs) - fallback} of {len(recs)}; centroid fallback for {fallback}")
    ok = True
    dated = [r for r in recs if "submitted_date" in r and "decision_date" in r]
    for grp in (dated, [r for r in recs if r not in dated]):
        for i in range(0, len(grp), 50):
            batch = grp[i:i + 50]
            # PostgREST needs identical keys in one batch: split further by key set
            for ks in {tuple(sorted(b.keys())) for b in batch}:
                ok = await E._supa_upsert([b for b in batch if tuple(sorted(b.keys())) == ks]) and ok
    if ok:
        now = datetime.now(timezone.utc).isoformat()
        print(f"[{who}] ✓ saved {len(recs)}")
        await E._supa_patch_council(cid, {"coverage_source": "new_councils_scraper", "last_scraped_at": now, "last_saved_at": now})
    else:
        print(f"[{who}] ✗ save failed")
        sys.exit(1)


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Lincoln / Barnsley / Rotherham   DRY_RUN={DRY_RUN}   "
          f"days back {DAYS_BACK}   only={ONLY or 'all'}")
    if not DRY_RUN and not (E.SUPABASE_URL and E.SUPABASE_KEY):
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set"); sys.exit(1)
    jobs = [("lincoln", lincoln), ("barnsley", barnsley), ("rotherham", rotherham)]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=E.BROWSER_ARGS)
        for who, fn in jobs:
            if ONLY and who not in ONLY:
                continue
            print(f"\n=== {who.upper()}")
            try:
                recs = await fn(browser)
            except Exception as e:
                print(f"[{who}] ✗ failed: {type(e).__name__}: {str(e)[:200]}")
                continue
            await save(who, recs)
        await browser.close()
    print(f"\nFinished in {minutes():.1f} minutes" + ("   (DRY_RUN: nothing written)" if DRY_RUN else ""))


if __name__ == "__main__":
    asyncio.run(main())
