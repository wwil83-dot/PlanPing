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

Findings from the first dry run: the table shows at most 20 rows ("20 results shown, there are 36 in total"), so
a window that reports more in total than it shows is split in half (down to one day) until nothing is cut off;
the table has no usable Received/Type columns, so each application's detail page is read for Received and the
decision date; there is a decision-date search (#decidedFrom/#decidedTo) which is swept as well, so decisions
on older applications are caught too.

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
    if any(x in s for x in ("refus", "reject", "dismiss", "not permitted", "appealed")):
        return "refused"
    if "undecided" in s or not s.strip():
        return "pending"
    if any(x in s for x in ("approv", "app cond", "grant", "permit", "allow", "no objection", "consent", "not required", "certif")):
        return "approved"
    if any(x in s for x in ("invalid", "returned", "closed", "cancel", "lapsed", "disposed", "no further action")):
        return "closed"
    return "pending"


_STATUS_SEEN: Counter = Counter()
_HEADERS_SHOWN = False

TYPE_BY_CODE = {"HH": "Householder", "PL": "Full Planning", "T": "Tree Works", "DOC": "Discharge of Conditions",
                "TC": "Tree Works - Conservation Area", "CLP": "Certificate of Lawfulness - Proposed",
                "CLE": "Certificate of Lawfulness - Existing", "NMA": "Non-material Amendment",
                "S73": "Variation of Condition", "L": "Listed Building Consent", "A": "Advertisement Consent",
                "AG": "Agricultural Prior Approval"}


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
    global _HEADERS_SHOWN
    if not _HEADERS_SHOWN:
        _HEADERS_SHOWN = True
        print(f"    ℹ results table headers: {head}")
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
                    "description": g(i_pro), "application_type": g(i_type) or TYPE_BY_CODE.get(ref.rsplit("/", 1)[-1].upper()),
                    "submitted_date": O._parse_ocellaweb_date(g(i_rec)), "raw_status": raw,
                    "status": status_of(raw), "council_url": url})
    return out


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass


_FORM_DUMPED = False
CAP_RE = re.compile(r"(\d+)\s+results?\s+shown,\s+there\s+are\s+(\d+)\s+in\s+total", re.I)


async def search_slice(ctx, f_from: str, f_to: str, a: date, b: date) -> tuple:
    """-> (rows, total_claimed or None). f_from/f_to are the field ids: receivedFrom/To or decidedFrom/To."""
    page = await ctx.new_page()
    try:
        for attempt in (1, 2, 3):
            try:
                await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45000)
                await settle(page)
                await page.fill(f"#{f_from}", a.strftime("%d-%m-%y"), timeout=8000)
                await page.fill(f"#{f_to}", b.strftime("%d-%m-%y"), timeout=8000)
                form = page.locator("form").filter(has=page.locator(f"#{f_from}"))
                btn = form.locator("button:has-text('Search')")
                if await btn.count() == 0:
                    btn = form.locator("input[type='submit'][value='Search']")
                await btn.first.click(timeout=8000)
                await settle(page)
                await asyncio.sleep(1)
                break
            except Exception as e:
                print(f"    ⚠ {f_from} {a:%d/%m}-{b:%d/%m} attempt {attempt} failed: {type(e).__name__}")
                if attempt == 3:
                    raise
                await asyncio.sleep(5 * attempt)
        html = await page.content()
        text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
        m = CAP_RE.search(text)
        return parse_table(html), (int(m.group(2)) if m else None)
    finally:
        await page.close()


async def collect(ctx, kind: str, a: date, b: date, out: dict, stats: dict):
    """Search a window; if the page says there are more results than it shows (it caps at 20), split it."""
    f_from, f_to = ("receivedFrom", "receivedTo") if kind == "received" else ("decidedFrom", "decidedTo")
    rows, total = await search_slice(ctx, f_from, f_to, a, b)
    stats["searches"] += 1
    if total and total > len(rows) and a < b:
        mid = a + (b - a) // 2
        await asyncio.sleep(1)
        await collect(ctx, kind, a, mid, out, stats)
        await collect(ctx, kind, mid + timedelta(days=1), b, out, stats)
        return
    if total and total > len(rows):
        stats["cut_days"].append(a.isoformat())
        print(f"    ⚠ {kind} single day {a:%d/%m/%Y}: shown {len(rows)} of {total} - cannot split further")
    for r in rows:
        r["_kind"] = kind
        out.setdefault(r["reference"], r)
        if kind == "decided":
            out[r["reference"]].update({"raw_status": r["raw_status"], "status": r["status"]})
    print(f"    ℹ {kind} {a:%d/%m/%Y}-{b:%d/%m/%Y}: {len(rows)} rows (total claimed {total}), {len(out)} unique")
    await asyncio.sleep(1)


async def sweep(ctx, kind: str, d_from: date, d_to: date) -> dict:
    out, stats = {}, {"searches": 0, "cut_days": []}
    start = d_from
    while start <= d_to:
        if minutes() > MAX_MINUTES:
            print(f"    ⚠ MAX_MINUTES reached in the {kind} sweep at {start:%d/%m/%Y}")
            break
        end = min(start + timedelta(days=SLICE_DAYS - 1), d_to)
        await collect(ctx, kind, start, end, out, stats)
        start = end + timedelta(days=1)
    print(f"    ℹ {kind} sweep {d_from:%d/%m/%Y}-{d_to:%d/%m/%Y}: {len(out)} applications in {stats['searches']} searches"
          + (f"; CUT OFF days {stats['cut_days']}" if stats["cut_days"] else ""))
    return out


_DETAIL_SHOWN = 0


async def read_detail(ctx, r: dict) -> dict:
    global _DETAIL_SHOWN
    p = await ctx.new_page()
    try:
        await p.goto(r["council_url"], wait_until="domcontentloaded", timeout=30000)
        await settle(p)
        t = re.sub(r"\s+", " ", await p.locator("body").inner_text())
    finally:
        await p.close()
    if _DETAIL_SHOWN < 3 and (r["status"] != "pending" or _DETAIL_SHOWN == 0):
        _DETAIL_SHOWN += 1
        print(f"    ℹ detail sample {r['reference']} (table status {r['raw_status']!r}): {t[:900]}")
    d = lambda pat: (O._parse_ocellaweb_date(re.search(pat, t).group(1)) if re.search(pat, t) else None)
    st = re.search(r"\bStatus\s+(.+?)\s+Proposal\b", t)
    return {"received": d(r"\bReceived\s+(\d{2}-\d{2}-\d{2})"), "validated": d(r"\bValidated\s+(\d{2}-\d{2}-\d{2})"),
            "decided": d(r"\b(?:Decided|Decision Date|Decision Issued|Date Decided)\s+(\d{2}-\d{2}-\d{2})"),
            "status": st.group(1).strip() if st else ""}


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Arun scraper ({SEARCH_URL})")
    print(f"Days back {DAYS_BACK}   slice {SLICE_DAYS}d   DRY_RUN {DRY_RUN}   max {MAX_MINUTES} min   council_id {COUNCIL_ID}")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and not (SUPABASE_URL and SUPABASE_KEY):
        print("ERROR: set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    today = date.today()
    d_from = today - timedelta(days=DAYS_BACK)
    details: dict = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=O.BROWSER_ARGS)
        ctx = await browser.new_context(**O.CONTEXT_OPTIONS)
        received = await sweep(ctx, "received", d_from, today)
        decided = await sweep(ctx, "decided", d_from, today)
        apps = {**received, **decided}
        print(f"\n    ℹ received {len(received)}, decided {len(decided)}, unique {len(apps)}")
        for n, (ref, r) in enumerate(apps.items(), 1):
            if minutes() > MAX_MINUTES:
                print(f"    ⚠ MAX_MINUTES reached after {n - 1} of {len(apps)} detail pages")
                break
            try:
                details[ref] = await read_detail(ctx, r)
            except Exception as e:
                print(f"    ⚠ detail {ref} failed: {type(e).__name__}")
            if n % 25 == 0:
                print(f"    ℹ detail pages {n}/{len(apps)}")
            await asyncio.sleep(0.7)
        await browser.close()

    rows = []
    for ref, r in apps.items():
        det = details.get(ref, {})
        r["submitted_date"] = (det.get("received") or det.get("validated") or r.get("submitted_date"))
        r["decision_date"] = det.get("decided") if r["status"] != "pending" else None
        if det.get("status") and r["status"] == "pending":
            r["status"] = status_of(det["status"])
        rows.append(r)
    print(f"    ℹ {len(rows)} applications; by status {dict(Counter(r['status'] for r in rows))}")
    print(f"    ℹ raw table status -> count: {dict(_STATUS_SEEN.most_common(20))}")
    print(f"    ℹ application types: {dict(Counter(r['application_type'] for r in rows).most_common(10))}")
    print(f"    ℹ reference suffixes: {dict(Counter(r['reference'].rsplit('/', 1)[-1] for r in rows).most_common(15))}")
    print(f"    ℹ missing postcode {sum(1 for r in rows if not r['postcode'])}; missing received date "
          f"{sum(1 for r in rows if not r['submitted_date'])}; decided without decision date "
          f"{sum(1 for r in rows if r['status'] != 'pending' and not r['decision_date'])}")
    if DRY_RUN:
        print("\n    --- 6 sample rows ---")
        for r in rows[:6]:
            print("   ", {k: v for k, v in r.items() if k not in ('raw_status', '_kind')})
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
                        "submitted_date": r["submitted_date"], "decision_date": r["decision_date"],
                        "council_url": r["council_url"], "lat": lat, "lng": lng, "source": SOURCE})
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
