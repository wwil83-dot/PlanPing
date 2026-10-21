#!/usr/bin/env python3
"""
PlanFind - Wokingham Borough Council scraper (classic ASP 'FastWebPL', via headless Chromium).

Built 2026-10-21 from three rounds of real recon on the UK runner (not assumed):

  * planning.wokingham.gov.uk RESETS plain-Python HTTP connections (TLS client filtering) but serves
    headless Chromium normally, so everything here runs inside Chromium (page.goto / click).
  * Advanced search (search.asp, form 'SearchForm', POST results.asp) has date fields
    DateValidStart/End, DateReceivedStart/End, DateDecidedStart/End (dd/mm/yyyy).
  * Results: 20 hits per page; a hidden input RecCount holds the total; page N is
    results.asp?Scroll=N&<same query string>  (the 'Next' link shows the exact pattern, which this
    script reuses, swapping Scroll).
  * Each result already carries: App. No., site address (with postcode), description, Received Date,
    Date Valid, iLap Ref, Decision Date, Decision. So NO per-application detail pages are fetched.
  * The portal gives no application type and no coordinates on these pages: application_type is left
    empty; coordinates come from the postcode (postcodes.io) with a council-centre fallback.

Two sweeps per run, merged by reference:
  1. Valid sweep   (Date Valid in the last WINDOW_DAYS days) -> new applications.
  2. Decided sweep (Decision Date in the last WINDOW_DAYS days) -> turns older 'pending' rows into
     approved/refused/withdrawn/closed. Decided wins on status and decision_date.

DRY_RUN=1 prints counts, the decision-text -> status table, sample rows and a link check, and WRITES
NOTHING.

HONEST LIMITATIONS:
  - Decision text vocabulary (54 values from the portal's own dropdown, 2026-10-21) is mapped by
    keyword; anything unmapped is printed once ('UNMAPPED DECISION') and saved as 'pending'.
  - Pre-application-only and enforcement-appeal records are skipped.
  - council_url is detail.asp?AltRef=<ref>. A direct link with no session returned HTTP 500 in recon
    when typed straight into the browser (it worked when clicked from the results page), so the DRY_RUN
    log opens it cold in a fresh browser context and reports what happens. Treat the link as
    unverified until that line is checked.

Env: SUPABASE_URL, SUPABASE_KEY (required unless DRY_RUN)
     WINDOW_DAYS (default 14; 60 for the first backfill), DRY_RUN ("1" = write nothing)
     PAGE_DELAY_SECONDS (default 2), MAX_PAGES (default 40), MAX_MINUTES (default 40)
     WOKINGHAM_COUNCIL_ID (optional)
"""
import asyncio
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx

B = "https://planning.wokingham.gov.uk/FastWebPL/"
SOURCE = "wokingham_scraper"
COUNCIL_NAME_LIKE = "*Wokingham*"
PER_PAGE = 20

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "14"))
PAGE_DELAY = float(os.environ.get("PAGE_DELAY_SECONDS", "2"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "40"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "40"))
DRY_RUN = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
START = time.monotonic()

SKIP_DECISION_WORDS = ("pre-app only", "enforcement appeal")
SKIP_DESC_WORDS = ("pre-application advice", "pre application advice")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline)
# ---------------------------------------------------------------------------
def _dmy(s) -> Optional[date]:
    if not s:
        return None
    try:
        d = datetime.strptime(s.strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None
    if d < date(1990, 1, 1) or d > date.today() + timedelta(days=366):
        return None
    return d


def _extract_postcode(text: str) -> Optional[str]:
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", (text or "").upper())
    return m.group(1) if m else None


def parse_results_text(t: str) -> list:
    """Parse inner_text of a results page into dicts."""
    out = []
    for blk in re.split(r"App\. No\.:", t)[1:]:
        ref = (blk.split() or [""])[0].strip()
        if not ref:
            continue
        m = re.search(r"Site Address:\s*(.*?)\s*View Details", blk, flags=re.S)
        address = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        m = re.search(r"Description:[ \t]*(.*?)\s*Received Date:", blk, flags=re.S)
        desc = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        m = re.search(r"Received Date:[ \t]*(\S*)\s+Date Valid:[ \t]*(\S*)", blk)
        received, valid = (m.group(1), m.group(2)) if m else ("", "")
        m = re.search(r"Decision Date:[ \t]*(\d{2}/\d{2}/\d{4})?", blk)
        decided = m.group(1) if m and m.group(1) else ""
        m = re.search(r"\n[ \t]*Decision:[ \t]*([^\n]*)", blk)
        decision = m.group(1).strip() if m else ""
        out.append({"reference": ref, "address": address, "description": desc, "received": _dmy(received),
                    "valid": _dmy(valid), "decided": _dmy(decided), "decision": decision})
    return out


_UNMAPPED: set = set()


def normalise_decision(decision: str, has_decision_date: bool) -> str:
    """Wokingham's own DecisionDescription vocabulary -> pending/approved/refused/withdrawn/closed."""
    s = (decision or "").strip().lower()
    if not s or s in ("pending decision", "pending"):
        return "pending"
    if "withdraw" in s:
        return "withdrawn"
    if "not inc in tpo" in s or s == "tree tpo":
        return "closed"
    if "secretary of state" in s or "called in" in s:
        return "closed"
    if "not discharged" in s or "not available" in s:
        return "refused"
    if "permission required" in s and "not" not in s:
        return "refused"
    if "not req" in s:
        return "approved"
    if any(x in s for x in ("invalid", "archive", "finally disposed", "not proceeded", "decline to determine",
                            "non determ", "superseded", "no reply", "quashed", "eia development", "completed",
                            "replied", "observations", "county matter", "mod application")):
        return "closed"
    if "partial objection" in s:
        return "refused"
    if "no objection" in s:
        return "approved"
    if "objection" in s or "refus" in s or "dismiss" in s:
        return "refused"
    if "split" in s:
        return "approved"
    if any(x in s for x in ("approv", "permitted", "consent", "allow", "discharge", "deemed", "dead/dangerous")):
        return "approved"
    if has_decision_date and s not in _UNMAPPED:
        _UNMAPPED.add(s)
        print(f"    ⚠ UNMAPPED DECISION {decision!r}: saved as 'pending' until a mapping is added")
    return "pending"


def to_record(r: dict) -> Optional[dict]:
    dl = r["decision"].lower()
    if any(w in dl for w in SKIP_DECISION_WORDS) or any(w in r["description"].lower() for w in SKIP_DESC_WORDS):
        return {"_skip": True}
    status = normalise_decision(r["decision"], bool(r["decided"]))
    submitted = r["valid"] or r["received"]
    return {
        "reference": r["reference"], "address": r["address"], "postcode": _extract_postcode(r["address"]),
        "description": r["description"] or None, "application_type": None, "status": status,
        "submitted_date": submitted.isoformat() if submitted else None,
        "decision_date": r["decided"].isoformat() if (r["decided"] and status != "pending") else None,
        "council_url": f"{B}detail.asp?AltRef={r['reference']}",
        "_decision_text": r["decision"],
    }


def merge(valid: dict, decided: dict) -> list:
    merged = dict(valid)
    for ref, rec in decided.items():
        if ref in merged:
            merged[ref]["status"] = rec["status"]
            merged[ref]["decision_date"] = rec["decision_date"]
            merged[ref]["_decision_text"] = rec["_decision_text"]
        else:
            merged[ref] = rec
    return list(merged.values())


# ---------------------------------------------------------------------------
# Browser sweeps
# ---------------------------------------------------------------------------
async def _body(page) -> str:
    return await page.inner_text("body")


async def sweep(page, kind: str, d_from: date, d_to: date) -> dict:
    """kind 'valid' (DateValidStart/End) or 'decided' (DateDecidedStart/End)."""
    pre = "DateValid" if kind == "valid" else "DateDecided"
    await page.goto(B + "welcome.asp", wait_until="networkidle", timeout=60000)
    await page.goto(B + "search.asp", wait_until="networkidle", timeout=60000)
    await page.fill(f"input[name={pre}Start]", d_from.strftime("%d/%m/%Y"))
    await page.fill(f"input[name={pre}End]", d_to.strftime("%d/%m/%Y"))
    await page.click("input#Submit")
    await page.wait_for_load_state("networkidle")
    total = await page.evaluate("() => { const e = document.querySelector('input[name=RecCount]'); return e ? e.value : null }")
    total = int(total) if total and str(total).isdigit() else None
    text = await _body(page)
    rows = parse_results_text(text)
    if total is None:
        total = len(rows)
    pages = max(1, math.ceil(total / PER_PAGE))
    next_href = await page.evaluate("""() => { const a = Array.from(document.querySelectorAll('a')).find(e => (e.innerText||'').trim() === 'Next');
        return a ? a.href : null }""")
    out = {}
    skipped = 0
    for r in rows:
        rec = to_record(r)
        if rec.get("_skip"):
            skipped += 1
        else:
            out[r["reference"]] = rec
    print(f"    ℹ {kind} sweep {d_from:%d/%m/%Y}-{d_to:%d/%m/%Y}: portal says {total} ({pages} page(s)); page 1 read {len(rows)}")
    if pages > 1 and not next_href:
        print("    ⚠ more than one page but no 'Next' link found; only page 1 collected")
    if next_href:
        u = urlparse(next_href)
        q = parse_qsl(u.query, keep_blank_values=True)
        for n in range(2, min(pages, MAX_PAGES) + 1):
            if (time.monotonic() - START) / 60 > MAX_MINUTES:
                print(f"    ⚠ MAX_MINUTES reached during the {kind} sweep at page {n}")
                break
            await asyncio.sleep(PAGE_DELAY)
            qn = [(k, str(n) if k == "Scroll" else v) for k, v in q]
            await page.goto(f"{u.scheme}://{u.netloc}{u.path}?{urlencode(qn)}", wait_until="networkidle", timeout=60000)
            rows = parse_results_text(await _body(page))
            for r in rows:
                rec = to_record(r)
                if rec.get("_skip"):
                    skipped += 1
                else:
                    out[r["reference"]] = rec
            print(f"    ℹ {kind} page {n}/{pages}: read {len(rows)}, {len(out)} kept so far")
            if not rows:
                break
        if pages > MAX_PAGES:
            print(f"    ⚠ {kind} sweep capped at MAX_PAGES={MAX_PAGES} of {pages}")
    if skipped:
        print(f"    ℹ {kind} sweep skipped {skipped} non-planning records")
    if total and len(out) + skipped < total:
        print(f"    ⚠ {kind} sweep collected {len(out) + skipped} of {total}")
    return out


async def link_check(browser, ref: str):
    """Open the saved council_url cold (fresh context, no cookies) and report what a visitor would see."""
    ctx = await browser.new_context(user_agent=UA, locale="en-GB", ignore_https_errors=True)
    pg = await ctx.new_page()
    try:
        for label, url in (("cold detail link", f"{B}detail.asp?AltRef={ref}"),):
            r = await pg.goto(url, wait_until="networkidle", timeout=45000)
            body = (await pg.inner_text("body"))[:120].replace("\n", " ")
            print(f"    ℹ LINK CHECK {label}: HTTP {r.status if r else None} | title {await pg.title()!r} | {body!r}")
    except Exception as e:
        print(f"    ⚠ link check failed: {e}")
    await ctx.close()


# ---------------------------------------------------------------------------
# Supabase (same REST pattern as every other scraper here)
# ---------------------------------------------------------------------------
def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}


async def _council_id() -> int:
    env = os.environ.get("WOKINGHAM_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name,system,coverage_source", "name": f"ilike.{COUNCIL_NAME_LIKE}"}, headers=_h())
        r.raise_for_status()
        rows = r.json()
    if len(rows) != 1:
        raise RuntimeError(f"Expected exactly one councils row matching Wokingham, found {len(rows)}: {rows}. Set WOKINGHAM_COUNCIL_ID.")
    print(f"    ℹ councils row: {rows[0]}")
    return rows[0]["id"]


async def _upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                             json=records, headers=headers)
            if r.status_code not in (200, 201, 204):
                print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                return False
            return True
    except Exception as e:
        print(f"    ✗ Upsert exception: {e}")
        return False


async def _patch_council(cid: int, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.patch(f"{SUPABASE_URL}/rest/v1/councils", params={"id": f"eq.{cid}"},
                      json=data, headers={**_h(), "Prefer": "return=minimal"})


async def geocode(postcodes: list) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    if not unique:
        return results
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(unique), 100):
            try:
                r = await c.post("https://api.postcodes.io/postcodes", json={"postcodes": unique[i:i + 100]})
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (item["result"]["latitude"], item["result"]["longitude"])
            except Exception as e:
                print(f"    ⚠ Geocoding batch failed: {e}")
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Wokingham scraper (FastWebPL via Chromium)")
    print(f"Window: {WINDOW_DAYS} days   DRY_RUN: {DRY_RUN}   page delay {PAGE_DELAY}s   max {MAX_MINUTES} min")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_KEY):
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    cid = None
    if SUPABASE_URL and SUPABASE_KEY:
        cid = await _council_id()
        print(f"[Wokingham] (council_id={cid})")
    today = date.today()
    d_from = today - timedelta(days=WINDOW_DAYS)

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=UA, locale="en-GB", ignore_https_errors=True,
                                        viewport={"width": 1400, "height": 1000})
        page = await ctx.new_page()
        valid = await sweep(page, "valid", d_from, today)
        await asyncio.sleep(PAGE_DELAY)
        decided = await sweep(page, "decided", d_from, today)
        apps = merge(valid, decided)
        if DRY_RUN and apps:
            await link_check(browser, apps[0]["reference"])
        await browser.close()

    print(f"    ℹ valid in window: {len(valid)}; decided in window: {len(decided)} "
          f"({sum(1 for r in decided if r not in valid)} not in the valid list); total {len(apps)}")
    print(f"    ℹ by status: {dict(Counter(a['status'] for a in apps))}")
    print("    ℹ decision text -> status (count):")
    for (txt, st), n in Counter((a["_decision_text"], a["status"]) for a in apps).most_common(40):
        print(f"        {txt or '(none)':<48} -> {st:<9} x{n}")
    print(f"    ℹ missing description: {sum(1 for a in apps if not a['description'])}; "
          f"missing postcode: {sum(1 for a in apps if not a['postcode'])}; "
          f"missing submitted date: {sum(1 for a in apps if not a['submitted_date'])}")

    if DRY_RUN:
        print("\n    --- 6 sample rows ---")
        for a in apps[:6]:
            print("   ", {k: v for k, v in a.items() if not k.startswith('_')})
        print("\nDRY_RUN: nothing written.")
        return
    if not apps:
        print("    ✗ nothing found - not saving")
        await _patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        return

    coords = await geocode([a["postcode"] for a in apps if a.get("postcode")])
    print(f"    Geocoded {len(coords)} of {len({(a['postcode'] or '').replace(' ', '') for a in apps if a['postcode']})} postcodes")
    for a in apps:
        pc = (a.get("postcode") or "").strip().upper().replace(" ", "")
        a["lat"], a["lng"] = coords.get(pc, (None, None))
    geocoded = [(a["lat"], a["lng"]) for a in apps if a.get("lat")]
    if geocoded:
        clat = statistics.median(g[0] for g in geocoded)
        clng = statistics.median(g[1] for g in geocoded)
        n = 0
        for a in apps:
            if not a.get("lat"):
                a["lat"], a["lng"] = clat, clng
                n += 1
        if n:
            print(f"    Council centroid fallback for {n} apps")

    records = [{
        "council_id": cid, "reference": a["reference"], "address": a["address"], "postcode": a["postcode"],
        "lat": a.get("lat"), "lng": a.get("lng"), "description": a["description"],
        "application_type": a["application_type"], "status": a["status"],
        "submitted_date": a["submitted_date"], "decision_date": a["decision_date"],
        "council_url": a["council_url"], "source": SOURCE,
    } for a in apps]
    saved, ok = 0, True
    for i in range(0, len(records), 50):
        batch = records[i:i + 50]
        if await _upsert(batch):
            saved += len(batch)
        else:
            ok = False
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        await _patch_council(cid, {"coverage_source": SOURCE, "last_scraped_at": now, "last_saved_at": now,
                                   "consecutive_empty_runs": 0, "active": True})
        print(f"    ✓ Saved {saved}")
    else:
        print(f"    ⚠ Partial save: {saved} of {len(records)}")
        if saved:
            await _patch_council(cid, {"last_saved_at": now, "consecutive_empty_runs": 0})
    print("\n" + "=" * 50)
    print(f"Finished in {(time.monotonic() - START) / 60:.1f} minutes")
    print(f"Applications saved: {saved}")


if __name__ == "__main__":
    asyncio.run(main())
