#!/usr/bin/env python3
"""
PlanFind - Boston + East Lindsey, one scraper for the shared "South & East Lincolnshire Councils Partnership"
Idox portal (publicaccess.e-lindsey.gov.uk).

WHY (recon 2026-10-23): Boston's planning service moved to this platform in the week of 14 Sep 2026.
New applications of BOTH councils now share one reference sequence (e.g. 01885/26/ABC at 'The Plantation
Rowdyke Road Wyberton Boston PE21 7AQ') and arrive with Ward = Not Available, Parish = Not Available and no
council field. The ward-based boston_scraper.py and east_lindsey_scraper.py therefore both miss every new
application. This scraper sweeps the portal by date only (no ward), 7-day windows, reads every results page
(10 per page, 'Next' link), and files each row to Boston or East Lindsey by ADDRESS:
  Boston      postcode district PE20 / PE21; or PE22 with a Boston-borough parish name; or, with no usable
              postcode, the word Boston (not in a street name) or a Boston-borough parish name.
  Skipped     South Holland postcodes (PE6/PE9/PE10/PE11/PE12): South Holland moves to this platform in
              December 2026 and has its own scraper until then.
  East Lindsey everything else.
Decided applications: a second sweep by decision date reads each decided application's Summary page for the
decision text and date, so they are saved as approved / refused / withdrawn rather than 'pending'.

Env: SUPABASE_URL, SUPABASE_KEY, BOSTON_COUNCIL_ID (560), SAVE_EAST_LINDSEY (0; 1 also saves East Lindsey,
needs EAST_LINDSEY_COUNCIL_ID 33), DAYS_BACK (14),
SLICE_DAYS (7), MAX_MINUTES (45), DRY_RUN (1 = write nothing), DETAIL_DELAY (1.0).
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

import east_lindsey_scraper as E  # tested helpers: geocode, _supa_upsert, _supa_patch_council, _normalise_status

BASE = E.BASE_URL
SUPABASE_URL = E.SUPABASE_URL
SUPABASE_KEY = E.SUPABASE_KEY
BOSTON_ID = int(os.environ.get("BOSTON_COUNCIL_ID", "0"))
EL_ID = int(os.environ.get("EAST_LINDSEY_COUNCIL_ID", "0"))
DAYS_BACK = int(os.environ.get("DAYS_BACK", "14"))
SLICE_DAYS = int(os.environ.get("SLICE_DAYS", "7"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "45"))
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
# East Lindsey's own ward-based scraper still works (its applications carry a ward), so by default this only
# SAVES Boston. East Lindsey rows are still classified and counted so the two can be compared.
SAVE_EL = os.environ.get("SAVE_EAST_LINDSEY", "0") == "1"
DETAIL_DELAY = float(os.environ.get("DETAIL_DELAY", "1.0"))
START = time.monotonic()
SOURCE = "lincs_shared_scraper"

BOSTON_PARISHES = ["algarkirk", "amber hill", "benington", "bicker", "butterwick", "fishtoft", "fosdyke", "frampton",
                   "freiston", "holland fen", "kirton", "leverton", "old leake", "sutterton", "swineshead", "wigtoft",
                   "wrangle", "wyberton"]
SOUTH_HOLLAND_PC = {"PE6", "PE9", "PE10", "PE11", "PE12"}
BOSTON_STREET_WORDS = r"(road|rd|street|st|lane|ln|avenue|way|west|east|drive|close|gate)"
PC_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s?(\d[A-Z]{2})\b")


def minutes() -> float:
    return (time.monotonic() - START) / 60


def classify(address: str) -> tuple:
    """-> (council, reason) with council in 'boston' | 'east_lindsey' | 'skip'."""
    a = address or ""
    low = a.lower()
    m = PC_RE.search(a.upper())
    district = m.group(1) if m else None
    parish = any(re.search(r"\b" + p + r"\b", low) for p in BOSTON_PARISHES)
    if district in ("PE20", "PE21"):
        return "boston", "postcode " + district
    if district == "PE22":
        if parish or re.search(r"\bboston\b", low):
            return "boston", "PE22 + Boston parish/name"
        return "east_lindsey", "PE22 other"
    if district in SOUTH_HOLLAND_PC:
        return "skip", "South Holland postcode " + district
    if district:
        return "east_lindsey", "postcode " + district
    if parish or re.search(r"\bboston\b(?!\s+" + BOSTON_STREET_WORDS + r"\b)", low):
        return "boston", "no postcode, Boston name/parish"
    return "east_lindsey", "no postcode"


def parse_page(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    box = soup.find("ul", id="searchresults")
    for it in (box.find_all("li", class_="searchresult") if box else []):
        link = it.find("a", class_="summaryLink")
        if not link:
            continue
        href = link.get("href") or ""
        url = href if href.startswith("http") else f"{BASE.rsplit('/', 1)[0]}{href}"
        desc = link.find("div", class_="summaryLinkTextClamp")
        addr = it.find("p", class_="address")
        st = it.select_one(".badge-status .value")
        meta = it.find("p", class_="metaInfo")
        ref, rec = "", ""
        if meta:
            for part in meta.get_text(" ", strip=True).split("·"):
                part = part.strip()
                if part.lower().startswith("ref"):
                    ref = part.split(":", 1)[1].strip() if ":" in part else ""
                elif part.lower().startswith("received"):
                    rec = part.split(":", 1)[1].strip() if ":" in part else ""
        if len(ref) < 3:
            continue
        address = addr.get_text(" ", strip=True) if addr else ""
        out.append({"reference": ref, "address": address, "postcode": E._extract_postcode(address),
                    "description": desc.get_text(" ", strip=True) if desc else "",
                    "raw_status": st.get_text(strip=True) if st else "",
                    "submitted_date": E._parse_received_date(rec), "council_url": url})
    return out


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass


async def run_window(page, f_start: str, f_end: str, a: date, b: date) -> tuple:
    """Date-only advanced search; returns (rows, total_claimed)."""
    for attempt in (1, 2, 3):
        try:
            await page.goto(f"{BASE}/search.do?action=advanced", wait_until="domcontentloaded", timeout=45000)
            await settle(page)
            await page.fill(f"input[name='{f_start}']", a.strftime("%d/%m/%Y"))
            await page.fill(f"input[name='{f_end}']", b.strftime("%d/%m/%Y"))
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await page.locator("input[type='submit'], button[type='submit']").first.click()
            await settle(page)
            break
        except Exception as e:
            print(f"    ⚠ search {a:%d/%m}-{b:%d/%m} attempt {attempt} failed: {type(e).__name__}")
            if attempt == 3:
                raise
            await asyncio.sleep(5 * attempt)
    rows, total, pages = [], None, 0
    while True:
        pages += 1
        html = await page.content()
        if total is None:
            m = re.search(r"Showing\s+\d+\s*-\s*\d+\s+of\s+(\d+)", re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")))
            total = int(m.group(1)) if m else None
        rows += parse_page(html)
        nxt = page.locator("a.next")
        if await nxt.count() == 0 or pages >= 40:
            break
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await nxt.first.click()
            await settle(page)
        except Exception as e:
            print(f"    ⚠ paging stopped at page {pages}: {type(e).__name__}")
            break
        await asyncio.sleep(1)
    return rows, total


async def sweep(page, kind: str, d_from: date, d_to: date) -> dict:
    f_start, f_end = (("date(applicationReceivedStart)", "date(applicationReceivedEnd)") if kind == "received"
                      else ("date(applicationDecisionStart)", "date(applicationDecisionEnd)"))
    out: dict = {}
    start = d_from
    while start <= d_to:
        if minutes() > MAX_MINUTES:
            print(f"    ⚠ MAX_MINUTES reached in the {kind} sweep at {start:%d/%m/%Y}")
            break
        end = min(start + timedelta(days=SLICE_DAYS - 1), d_to)
        rows, total = await run_window(page, f_start, f_end, start, end)
        for r in rows:
            out[r["reference"]] = r
        flag = "" if total is None or total == len(rows) else f"   ⚠ page said {total}, read {len(rows)}"
        print(f"    ℹ {kind} {start:%d/%m/%Y}-{end:%d/%m/%Y}: {len(rows)} rows (claimed {total}), {len(out)} total{flag}")
        start = end + timedelta(days=1)
        await asyncio.sleep(1)
    return out


_UNMAPPED: set = set()


def decision_to_status(text: str) -> Optional[str]:
    s = (text or "").lower()
    if not s:
        return None
    if "withdraw" in s:
        return "withdrawn"
    if "refus" in s or "objection" in s and "no objection" not in s:
        return "refused"
    if any(x in s for x in ("approv", "no objection", "not required", "granted", "permit")):
        return "approved"
    if any(x in s for x in ("required", "responded", "deferred", "called in", "revoked", "section 106", "esco")):
        return "closed"
    if "non-determination" in s:
        return "pending"
    if s not in _UNMAPPED:
        _UNMAPPED.add(s)
        print(f"    ⚠ UNMAPPED DECISION {text!r}: left as the results-page status")
    return None


async def read_decision(page, url: str) -> tuple:
    """(decision_text, decision_date) from the application's Summary tab."""
    await page.goto(re.sub(r"activeTab=\w+", "activeTab=summary", url), wait_until="domcontentloaded", timeout=45000)
    await settle(page)
    txt = re.sub(r"\s+", " ", await page.inner_text("body"))
    m = re.search(r"\bDecision\s+(.+?)\s+(?:Decision Issued Date|Decision Level|Appeal|Actual Decision Level|Expected Decision Level)", txt)
    d = re.search(r"Decision Issued Date\s+\w{3} (\d{1,2} \w{3} \d{4})", txt)
    dd = None
    if d:
        try:
            dd = datetime.strptime(d.group(1), "%d %b %Y").date()
        except ValueError:
            pass
    return (m.group(1).strip() if m else ""), dd


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Boston + East Lindsey shared-portal scraper")
    print(f"Days back {DAYS_BACK}   slice {SLICE_DAYS}d   DRY_RUN {DRY_RUN}   max {MAX_MINUTES} min   "
          f"boston_id {BOSTON_ID}   east_lindsey_id {EL_ID}")
    print(f"SUPABASE: {'set' if SUPABASE_URL and SUPABASE_KEY else 'NOT SET'}\n")
    if not DRY_RUN and not (SUPABASE_URL and SUPABASE_KEY and BOSTON_ID and (EL_ID or not SAVE_EL)):
        print("ERROR: set SUPABASE_URL, SUPABASE_KEY and BOSTON_COUNCIL_ID (and EAST_LINDSEY_COUNCIL_ID if SAVE_EAST_LINDSEY=1)")
        sys.exit(1)

    today = date.today()
    d_from = today - timedelta(days=DAYS_BACK)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=E.BROWSER_ARGS)
        ctx = await browser.new_context(**E.CONTEXT_OPTIONS)
        page = await ctx.new_page()

        received = await sweep(page, "received", d_from, today)
        decided = await sweep(page, "decided", d_from, today)
        refs = list(dict.fromkeys(list(decided) + list(received)))
        print(f"\n    ℹ received {len(received)}, decided {len(decided)}, unique {len(refs)}")

        # decision text for decided applications
        dec_info, shown = {}, 0
        for n, ref in enumerate(decided, 1):
            if minutes() > MAX_MINUTES:
                print("    ⚠ MAX_MINUTES reached while reading decisions")
                break
            row = decided[ref]
            who = classify(row["address"])[0]
            if who == "skip" or (who == "east_lindsey" and not SAVE_EL):
                continue
            try:
                dec_info[ref] = await read_decision(page, row["council_url"])
            except Exception as e:
                print(f"    ⚠ decision read failed for {ref}: {type(e).__name__}")
                continue
            if shown < 6:
                shown += 1
                print(f"    ℹ sample decision {ref}: {dec_info[ref][0]!r} on {dec_info[ref][1]}")
            if n % 25 == 0:
                print(f"    ℹ decisions {n}/{len(decided)}")
            await asyncio.sleep(DETAIL_DELAY)
        await ctx.close()
        await browser.close()

    records = {"boston": [], "east_lindsey": []}
    reasons, skipped = Counter(), []
    for ref in refs:
        row = received.get(ref) or decided.get(ref)
        who, why = classify(row["address"])
        reasons[(who, why)] += 1
        if who == "skip":
            skipped.append(ref)
            continue
        status = E._normalise_status(row["raw_status"])
        dtext, ddate = dec_info.get(ref, ("", None))
        dstat = decision_to_status(dtext) if (ref in decided and dtext) else None
        if ref in decided and dstat:
            status = dstat
        elif ref in decided and row["raw_status"].lower().startswith("decided") and status == "pending":
            status = "closed"
        records[who].append({
            "reference": ref, "address": row["address"] or None, "postcode": row["postcode"],
            "description": row["description"] or None, "application_type": "Planning", "status": status,
            "submitted_date": row["submitted_date"].isoformat() if row["submitted_date"] else None,
            "decision_date": ddate.isoformat() if (ddate and status != "pending") else None,
            "council_url": row["council_url"], "source": SOURCE,
        })

    print("\n    ℹ classification (council, reason) -> count")
    for (who, why), n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"        {who:<13} {why:<34} {n}")
    for who in records:
        print(f"    ℹ {who}: {len(records[who])} applications, by status {dict(Counter(r['status'] for r in records[who]))}")
    print(f"    ℹ skipped (South Holland postcodes): {len(skipped)}")
    print("\n    --- Boston sample ---")
    for r in records["boston"][:8]:
        print("   ", r["reference"], "|", r["address"], "|", r["status"], "|", r["submitted_date"], "|", r["decision_date"])
    print("    --- East Lindsey rows with NO postcode (check these are not Boston) ---")
    for r in [r for r in records["east_lindsey"] if not r["postcode"]][:10]:
        print("   ", r["reference"], "|", r["address"])

    if DRY_RUN:
        print("\nDRY_RUN: nothing written.")
        return
    for who, cid in (("boston", BOSTON_ID),) + ((("east_lindsey", EL_ID),) if SAVE_EL else ()):
        recs = records[who]
        if not recs:
            await E._supa_patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
            print(f"[{who}] nothing to save")
            continue
        coords = await E.geocode([r["postcode"] for r in recs if r["postcode"]])
        fallback = 0
        for r in recs:
            key = (r["postcode"] or "").upper().replace(" ", "")
            r["lat"], r["lng"] = coords.get(key, (None, None))
            fallback += r["lat"] is None
            r["council_id"] = cid
        print(f"[{who}] geocoded {len(recs) - fallback} of {len(recs)}; centroid fallback for {fallback}")
        ok = True
        for i in range(0, len(recs), 50):
            ok = await E._supa_upsert(recs[i:i + 50]) and ok
        if ok:
            print(f"[{who}] ✓ saved {len(recs)}")
            await E._supa_patch_council(cid, {"coverage_source": SOURCE, "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                                              "last_saved_at": datetime.now(timezone.utc).isoformat()})
        else:
            print(f"[{who}] ✗ save failed")
            sys.exit(1)
    print(f"\nFinished in {minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
