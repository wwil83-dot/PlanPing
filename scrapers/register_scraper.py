#!/usr/bin/env python3
"""
PlanFind - 'Planning Register' family scraper: South Hams, West Devon, Wealden.

Same server-rendered ASP.NET MVC product as North Devon (northdevon_scraper.py), recon'd 2026-10-22
with register_probe.py on the UK runner. Differences handled here:
  * a /Disclaimer page: GET /Disclaimer?returnUrl=%2FSearch%2FAdvanced then POST
    /Disclaimer/Accept?returnUrl=... (cookie session) before /Search/Advanced shows its form;
  * South Hams / West Devon (planning-register.co.uk): the form posts to /Search/SiteResults and has
    DateReceivedFrom/To and DateIssuedFrom/To; planning + tree preservation orders; table columns
    Application Number | Location | Proposal | Status;
  * Wealden (planning.wealden.gov.uk): form posts to /Search/Results, has DateReceivedFrom/To and
    DateDeterminedFrom/To, also searches building control + enforcement (switched OFF here); table
    columns Application Number | Site Ref (UPRN) | Location | Description | Date Validated | Decision
    Date | Status Decision.
  * Results table columns are located by their HEADER names, not by position.
  * Result totals are not relied on (the wording of the count line was not recognised in recon):
    pages are read until a page adds no new application. Results paging is the North Devon pattern
    POST /Search/ChangeResultsPerPage (50) then GET /Search/Results/<n>/50.
Each unique application then gets one detail-page request (/Planning/Display/<ref>) for valid date,
type, proposal, panel decision and decision date.

Env: COUNCIL = southhams | westdevon | wealden   (required)
     SUPABASE_URL, SUPABASE_KEY (unless DRY_RUN), WINDOW_DAYS (14; 60 for a backfill),
     VALIDATION_LAG_DAYS (35), DRY_RUN ("1" = write nothing), DETAIL_DELAY_SECONDS (1),
     MAX_MINUTES (45), <COUNCIL>_COUNCIL_ID optional override (SOUTHHAMS_/WESTDEVON_/WEALDEN_)

HONEST LIMITATIONS: unmapped status text is printed once and saved as 'pending'. 'Decision Issued' /
'Decision Made' alone are ambiguous; the detail page's decision value is consulted first. Wealden
planning + tree-preservation records only (building control and enforcement are not searched).
"""
import asyncio
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

import northdevon_scraper as N  # tested helpers: _strip, _dmy, _extract_postcode, _word_status, normalise_status, ...

CONFIGS = {
    "southhams": dict(base="https://southhams.planning-register.co.uk", like="*South Hams*", env="SOUTHHAMS",
                      action="/Search/SiteResults", source="register_scraper_southhams",
                      flags={"SearchPlanning": ["true", "false"], "SearchTreePreservationOrders": ["true", "false"]},
                      recv=("DateReceivedFrom", "DateReceivedTo"), dec=("DateIssuedFrom", "DateIssuedTo")),
    "westdevon": dict(base="https://westdevon.planning-register.co.uk", like="*West Devon*", env="WESTDEVON",
                      action="/Search/SiteResults", source="register_scraper_westdevon",
                      flags={"SearchPlanning": ["true", "false"], "SearchTreePreservationOrders": ["true", "false"]},
                      recv=("DateReceivedFrom", "DateReceivedTo"), dec=("DateIssuedFrom", "DateIssuedTo")),
    "wealden": dict(base="https://planning.wealden.gov.uk", like="*Wealden*", env="WEALDEN",
                    action="/Search/Results", source="register_scraper_wealden",
                    flags={"SearchPlanning": ["true", "false"], "SearchBuildingControl": "false",
                           "SearchEnforcement": "false", "SearchTreePreservationOrders": ["true", "false"],
                           "Outstanding": "false"},
                    recv=("DateReceivedFrom", "DateReceivedTo"), dec=("DateDeterminedFrom", "DateDeterminedTo")),
}
COUNCIL = os.environ.get("COUNCIL", "").strip().lower()
CFG = CONFIGS.get(COUNCIL)

SUPABASE_URL, SUPABASE_KEY = N.SUPABASE_URL, N.SUPABASE_KEY
WINDOW_DAYS = N.WINDOW_DAYS
LAG_DAYS = N.LAG_DAYS
DETAIL_DELAY = N.DETAIL_DELAY
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "45"))
DRY_RUN = N.DRY_RUN
UA = N.UA
START = N.START
SKIP_TYPE_WORDS = N.SKIP_TYPE_WORDS


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline)
# ---------------------------------------------------------------------------
def parse_results(page: str) -> list:
    """Rows with a /Planning/Display/ link -> dicts; columns located by header name."""
    header = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        if re.search(r"<th\b", tr, flags=re.I):
            header = [N._strip(c).lower() for c in re.findall(r"<th\b[^>]*>(.*?)</th>", tr, flags=re.I | re.S)]
            break

    def col(*needles):
        for i, h in enumerate(header):
            if any(n in h for n in needles):
                return i
        return None
    i_ref, i_loc = col("application number"), col("location")
    i_desc, i_valid = col("proposal", "description"), col("validated", "date valid")
    i_dec, i_stat = col("decision date"), col("status")
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        m = re.search(r'href="([^"]*/Planning/Display/[^"]*)"', tr, flags=re.I)
        if not m:
            continue
        cells = [N._strip(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, flags=re.I | re.S)]

        def g(i, default=""):
            return cells[i] if i is not None and i < len(cells) else default
        ref = g(i_ref if i_ref is not None else 0)
        if not ref:
            continue
        out.append({"reference": ref, "href": m.group(1).replace("&amp;", "&"), "location": g(i_loc),
                    "description": g(i_desc), "valid_text": g(i_valid), "decision_date_text": g(i_dec),
                    "results_status": g(i_stat, cells[-1] if cells else "")})
    return out


_UNMAPPED_R: set = set()


def _w(s: str) -> Optional[str]:
    """One portal phrase -> status, or None if it says nothing decisive."""
    s = (s or "").strip().lower()
    if not s or s in ("n/a", "pending", "pending decision", "-") or s in (
            "allocated to officer", "under consideration", "within consultation period"):
        return None
    if "withdraw" in s:
        return "withdrawn"
    if "eia" in s or "do not wish to comment" in s or "observations" in s:
        return "closed"
    if any(x in s for x in ("invalid", "returned", "closed", "cancel", "not proceed", "finally disposed",
                            "no further action", "not determined", "entered in error", "not valid")):
        return "closed"
    if "no objection" in s or "no formal objection" in s:
        return "approved"
    if "objection" in s:
        return "refused"
    if "refus" in s or "reject" in s or "dismiss" in s or "not permit" in s or "unlawful" in s or "not lawful" in s:
        return "refused"
    if "permission required" in s and "not" not in s:
        return "refused"
    if "approval required and given" in s:
        return "approved"
    if "approval required" in s or "details required" in s:
        return "closed"
    if "split" in s or "part consent" in s or "partial" in s:
        return "approved"
    if any(x in s for x in ("approv", "grant", "permit", "allow", "consent", "not required", "certified",
                            "lawful", "discharge", "deemed", "agree", "accept")):
        return "approved"
    return None


def reg_status(results_status: str, detail_status: str, panel: str, has_decision_date: bool) -> str:
    """Withdrawal anywhere wins; then an explicit Approved/Refused in the results status (South Hams /
    West Devon); then the detail panel's decision text; then the other statuses."""
    texts = (results_status, detail_status, panel)
    if any("withdraw" in (t or "").lower() for t in texts):
        return "withdrawn"
    r = (results_status or "").strip().lower()
    if r in ("approved", "refused"):
        return r
    for t in (panel, detail_status, results_status):
        v = _w(t)
        if v:
            return v
    if has_decision_date:
        label = f"{results_status} | {detail_status} | {panel}"
        if label not in _UNMAPPED_R:
            _UNMAPPED_R.add(label)
            print(f"    ⚠ UNMAPPED STATUS {label!r}: saved as 'pending' until a mapping is added")
    return "pending"


def parse_detail(text: str) -> dict:
    det = N.parse_detail_text(text)
    if det.get("address"):
        det["address"] = re.sub(r"\s+UPRN\s+\S+\s*$", "", det["address"]).strip() or None
    i0 = text.find("Summary Application Number")
    m = re.search(r"(?<!Target )Decision Date\s+(\d{2}/\d{2}/\d{4})", text[i0:] if i0 >= 0 else text)
    det["decided"] = N._dmy(m.group(1)) if m else None  # 'Target Decision Date' is a future estimate
    if not det.get("panel_decision"):
        i = text.find("Summary Application Number")
        m = re.search(r"Appeal Decision\s+\S.*?\s+Decision\s+(.*?)\s+Back to top", text[i:] if i >= 0 else text)
        if m:
            det["panel_decision"] = m.group(1).strip()
    return det


# ---------------------------------------------------------------------------
# HTTP sweeps
# ---------------------------------------------------------------------------
async def accept_disclaimer(client: httpx.AsyncClient):
    base = CFG["base"]
    r = await N._req(client, "GET", f"{base}/Disclaimer?returnUrl=%2FSearch%2FAdvanced")
    tok = N._token(r.text)
    await N._req(client, "POST", f"{base}/Disclaimer/Accept?returnUrl=%2FSearch%2FAdvanced",
                 data={"__RequestVerificationToken": tok, "AcceptDisclaimer": "true"},
                 headers={"Referer": f"{base}/Disclaimer?returnUrl=%2FSearch%2FAdvanced"})


async def advanced_token(client: httpx.AsyncClient) -> str:
    base = CFG["base"]
    for attempt in (1, 2):
        r = await N._req(client, "GET", f"{base}/Search/Advanced")
        if "DateReceivedFrom" in r.text:
            tok = N._token(r.text)
            if tok:
                return tok
        print(f"    ℹ disclaimer not accepted yet (attempt {attempt}); accepting")
        await accept_disclaimer(client)
        await asyncio.sleep(1)
    raise RuntimeError("Could not reach the advanced search form after accepting the disclaimer")


SLICE_DAYS = int(os.environ.get("SLICE_DAYS", "7"))
CAP = 50  # rows per results page; a window that returns this many may be truncated, so it is split


async def run_window(client: httpx.AsyncClient, kind: str, a: date, b: date) -> list:
    base = CFG["base"]
    f_from, f_to = CFG["recv"] if kind == "received" else CFG["dec"]
    tok = await advanced_token(client)
    form = {"__RequestVerificationToken": tok, "AdvancedSearch": "True", **CFG["flags"],
            f_from: a.strftime("%d/%m/%Y"), f_to: b.strftime("%d/%m/%Y")}
    await N._req(client, "POST", base + CFG["action"], data=form, headers={"Referer": f"{base}/Search/Advanced"})
    r = await N._req(client, "POST", f"{base}/Search/ChangeResultsPerPage",
                     data={"__RequestVerificationToken": tok, "resultsPerPage": str(CAP)},
                     headers={"Referer": f"{base}/Search/Results"})
    return parse_results(r.text)


async def collect(client, kind: str, a: date, b: date, out: dict, stats: dict):
    """Read one date window; if it returns a full page it may be capped/paged, so split it in half.
    (Recon logs showed each 60-90 day window returning exactly 50 rows with page 2 identical to page 1,
    so these portals cap or ignore paging; narrow windows avoid depending on it.)"""
    await asyncio.sleep(1)
    rows = await run_window(client, kind, a, b)
    stats["searches"] += 1
    if len(rows) >= CAP and a < b:
        mid = a + (b - a) // 2
        await collect(client, kind, a, mid, out, stats)
        await collect(client, kind, mid + 1, b, out, stats)
        return
    if len(rows) >= CAP:
        stats["capped_days"].append(a.isoformat())
        print(f"    ⚠ {kind} single day {a:%d/%m/%Y} returned {len(rows)} rows (may be truncated)")
    new = 0
    for row in rows:
        if row["reference"] not in out:
            new += 1
        out[row["reference"]] = row
    print(f"    ℹ {kind} {a:%d/%m/%Y}-{b:%d/%m/%Y}: {len(rows)} rows, {new} new, {len(out)} total")


async def sweep(client: httpx.AsyncClient, kind: str, d_from: date, d_to: date) -> dict:
    out: dict = {}
    stats = {"searches": 0, "capped_days": []}
    start = d_from
    while start <= d_to:
        if (time.monotonic() - START) / 60 > MAX_MINUTES:
            print(f"    ⚠ MAX_MINUTES reached during the {kind} sweep at {start:%d/%m/%Y}")
            break
        end = min(start + timedelta(days=SLICE_DAYS - 1), d_to)
        await collect(client, kind, start, end, out, stats)
        start = end + timedelta(days=1)
    print(f"    ℹ {kind} sweep {d_from:%d/%m/%Y}-{d_to:%d/%m/%Y}: collected {len(out)} in {stats['searches']} searches"
          + (f"; capped days: {stats['capped_days']}" if stats["capped_days"] else ""))
    return out


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------
async def council_id() -> int:
    env = os.environ.get(f"{CFG['env']}_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name,system,coverage_source", "name": f"ilike.{CFG['like']}"},
                        headers=N._h())
        r.raise_for_status()
        rows = r.json()
    if len(rows) != 1:
        raise RuntimeError(f"Expected one councils row matching {CFG['like']}, found {len(rows)}: {rows}")
    print(f"    ℹ councils row: {rows[0]}")
    return rows[0]["id"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    if not CFG:
        print(f"ERROR: set COUNCIL to one of {sorted(CONFIGS)}")
        sys.exit(1)
    base = CFG["base"]
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind register scraper: {COUNCIL} ({base})")
    print(f"Window: {WINDOW_DAYS} days (+{LAG_DAYS} lag on received)   DRY_RUN: {DRY_RUN}   "
          f"detail delay {DETAIL_DELAY}s   max {MAX_MINUTES} min")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_KEY):
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    cid = await council_id() if (SUPABASE_URL and SUPABASE_KEY) else None
    if cid:
        print(f"[{COUNCIL}] (council_id={cid})")
    today = date.today()
    d_from = today - timedelta(days=WINDOW_DAYS)

    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"}) as client:
        await accept_disclaimer(client)
        received = await sweep(client, "received", d_from - timedelta(days=LAG_DAYS), today)
        determined = await sweep(client, "determined", d_from, today)
        refs = list(dict.fromkeys(list(determined) + list(received)))
        print(f"    ℹ received list {len(received)}, determined list {len(determined)}, unique {len(refs)}")

        apps, failed, skipped, timed_out = [], 0, 0, False
        for n, ref in enumerate(refs, 1):
            if (time.monotonic() - START) / 60 > MAX_MINUTES:
                timed_out = True
                print(f"    ⚠ MAX_MINUTES reached after {n - 1} of {len(refs)} detail pages; saving what we have")
                break
            row = determined.get(ref) or received.get(ref)
            try:
                r = await N._req(client, "GET", base + row["href"] if row["href"].startswith("/") else row["href"])
                det = parse_detail(N._strip(r.text))
            except Exception as e:
                failed += 1
                print(f"    ⚠ detail {ref} failed: {e}")
                continue
            if det["type"] and any(w in det["type"].lower() for w in SKIP_TYPE_WORDS):
                skipped += 1
                await asyncio.sleep(DETAIL_DELAY)
                continue
            address = det["address"] or row["location"]
            decided = N._dmy(row["decision_date_text"]) or det["decided"]
            status = reg_status(row["results_status"], det["status"] or "", det["panel_decision"] or "", bool(decided))
            valid = det["valid"] or N._dmy(row["valid_text"])
            submitted = valid or det["received"]
            apps.append({
                "reference": ref, "address": address, "postcode": N._extract_postcode(address),
                "description": det["proposal"] or row["description"] or None, "application_type": det["type"],
                "status": status, "submitted_date": submitted.isoformat() if submitted else None,
                "decision_date": decided.isoformat() if (decided and status != "pending") else None,
                "council_url": base + row["href"] if row["href"].startswith("/") else row["href"],
                "_combo": (row["results_status"], det["status"] or "", det["panel_decision"] or ""),
            })
            if n % 25 == 0:
                print(f"    ℹ detail pages {n}/{len(refs)}")
            await asyncio.sleep(DETAIL_DELAY)

    print(f"    ℹ parsed {len(apps)}; skipped {skipped} non-planning types; detail failures {failed}")
    print(f"    ℹ by status: {dict(Counter(a['status'] for a in apps))}")
    print("    ℹ results status | detail status | panel decision -> status (count):")
    for (combo, st), n in Counter((a["_combo"], a["status"]) for a in apps).most_common(40):
        print(f"        {combo[0]!r:<34} | {combo[1]!r:<26} | {combo[2]!r:<26} -> {st:<9} x{n}")
    print(f"    ℹ application types: {dict(Counter(a['application_type'] for a in apps).most_common(12))}")
    print(f"    ℹ missing proposal: {sum(1 for a in apps if not a['description'])}; "
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
        await N._patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        return

    coords = await N.geocode([a["postcode"] for a in apps if a.get("postcode")])
    print(f"    Geocoded {len(coords)} of {len({(a['postcode'] or '').replace(' ', '') for a in apps if a['postcode']})} postcodes")
    for a in apps:
        pc = (a.get("postcode") or "").strip().upper().replace(" ", "")
        a["lat"], a["lng"] = coords.get(pc, (None, None))
    geocoded = [(a["lat"], a["lng"]) for a in apps if a.get("lat")]
    if geocoded:
        clat = statistics.median(g[0] for g in geocoded)
        clng = statistics.median(g[1] for g in geocoded)
        k = 0
        for a in apps:
            if not a.get("lat"):
                a["lat"], a["lng"] = clat, clng
                k += 1
        if k:
            print(f"    Council centroid fallback for {k} apps")

    records = [{
        "council_id": cid, "reference": a["reference"], "address": a["address"], "postcode": a["postcode"],
        "lat": a.get("lat"), "lng": a.get("lng"), "description": a["description"],
        "application_type": a["application_type"], "status": a["status"],
        "submitted_date": a["submitted_date"], "decision_date": a["decision_date"],
        "council_url": a["council_url"], "source": CFG["source"],
    } for a in apps]
    saved, ok = 0, True
    for i in range(0, len(records), 50):
        batch = records[i:i + 50]
        if await N._upsert(batch):
            saved += len(batch)
        else:
            ok = False
    now = datetime.now(timezone.utc).isoformat()
    if ok and not timed_out:
        await N._patch_council(cid, {"coverage_source": CFG["source"], "last_scraped_at": now,
                                     "last_saved_at": now, "consecutive_empty_runs": 0, "active": True})
        print(f"    ✓ Saved {saved}")
    else:
        print(f"    ⚠ Partial save: {saved} of {len(records)}")
        if saved:
            await N._patch_council(cid, {"last_saved_at": now, "consecutive_empty_runs": 0})
    print("\n" + "=" * 50)
    print(f"Finished in {(time.monotonic() - START) / 60:.1f} minutes")
    print(f"Applications saved: {saved}")


if __name__ == "__main__":
    asyncio.run(main())
