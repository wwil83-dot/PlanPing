#!/usr/bin/env python3
"""
PlanFind - North Devon Council scraper (planning.northdevon.gov.uk, server-rendered ASP.NET MVC).

Built 2026-10-19 from three rounds of real recon on the UK runner (not assumed):

  * No JSON API. Plain HTTP works (no browser): GET /Search/Advanced gives an anti-forgery token
    + cookie; POST /Search/Results with it returns (after a 302) the result list.
  * Searchable dates: DateReceivedFrom/To and DateDeterminedFrom/To (DD/MM/YYYY). There is NO
    Date Valid filter (valid date is only a results column).
  * Results: 'Your search has returned N results. Currently showing page 1 of P (10 results per
    page)'. Table columns: Module | Application Number | Location | Date Valid (dd-mm-yyyy) | Status.
    POST /Search/ChangeResultsPerPage (resultsPerPage=50, same token) redirects to
    /Search/Results/1/50 and shows 50 rows; later pages are /Search/Results/<n>/50
    (the 10-per-page route /Search/Results/<n> is the fallback).
  * Detail page GET /Planning/Display/<ref> (static): Summary (Location Address, Proposal, Status,
    Application Type), Important Dates (Received, Valid, Decision), and a top panel
    'Committee Meeting <x> Decision <outcome> Decision Date <d>'.
  * No coordinates on the portal -> postcodes.io on the postcode in the address, falling back to
    the council centre.

Two sweeps per run, merged by reference:
  1. Received sweep   (Received in the last WINDOW_DAYS + VALIDATION_LAG_DAYS days, because an
     application becomes valid some weeks after it is received and Valid has no filter).
  2. Determined sweep (Decision date in the last WINDOW_DAYS days) -> turns older 'pending' rows
     into approved/refused/withdrawn.
Each unique application then gets ONE detail-page request (about 1 s apart) for proposal, type and
dates; nothing else is fetched.

HONEST LIMITATIONS:
  - Decision/status vocabulary seen so far: 'Decision Approved', 'Decision Issued', 'Certificate
    Approved', 'Publicity Period', 'Valid awaiting Officer allocation', panel decision 'Pending'.
    'Decision Issued' is ambiguous; the detail panel's Decision value is consulted FIRST. The
    DRY_RUN log prints every (results status | detail status | panel decision) -> status combination
    so any wrong mapping is visible. Unmapped values are printed once ('UNMAPPED') and saved as
    'pending' while there is no decision date.
  - The detail-page text parse follows the labels seen in one recon page; fields that cannot be
    read are left empty rather than guessed.
  - Enforcement and appeals are not searched (planning only).

Env: SUPABASE_URL, SUPABASE_KEY (required unless DRY_RUN)
     WINDOW_DAYS (default 14; use 60 for the first backfill)
     VALIDATION_LAG_DAYS (default 35), DRY_RUN ("1" = write nothing)
     DETAIL_DELAY_SECONDS (default 1), MAX_MINUTES (default 40), NORTHDEVON_COUNCIL_ID (optional)
"""
import asyncio
import html as htmllib
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

BASE = "https://planning.northdevon.gov.uk"
SOURCE = "northdevon_scraper"
COUNCIL_NAME_LIKE = "*North Devon*"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "14"))
LAG_DAYS = int(os.environ.get("VALIDATION_LAG_DAYS", "35"))
DETAIL_DELAY = float(os.environ.get("DETAIL_DELAY_SECONDS", "1"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "40"))
DRY_RUN = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
START = time.monotonic()

SKIP_TYPE_WORDS = ("pre app", "pre-app", "preapp", "pre application", "pre-application",
                   "validation surgery", "entered in error", "enforcement")


# ---------------------------------------------------------------------------
# Parsing helpers (pure functions - unit-tested offline)
# ---------------------------------------------------------------------------
def _strip(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _dmy(s, sep="/") -> Optional[date]:
    if not s:
        return None
    try:
        d = datetime.strptime(s.strip()[:10], f"%d{sep}%m{sep}%Y").date()
    except ValueError:
        return None
    if d < date(1990, 1, 1) or d > date.today() + timedelta(days=366):
        return None
    return d


def _extract_postcode(text: str) -> Optional[str]:
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", (text or "").upper())
    return m.group(1) if m else None


def parse_results(page: str):
    """-> (total or None, [(ref, location, valid_date_text, status_text)])"""
    m = re.search(r"returned\s+(\d+)\s+results?", _strip(page), flags=re.I)
    total = int(m.group(1)) if m else None
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = [_strip(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, flags=re.I | re.S)]
        if len(cells) >= 5 and re.search(r"/Planning/Display/", tr, flags=re.I):
            rows.append((cells[1], cells[2], cells[3], cells[4]))
    return total, rows


def parse_detail_text(text: str) -> dict:
    """Parse the stripped text of /Planning/Display/<ref>."""
    out = {"address": None, "proposal": None, "status": None, "type": None,
           "received": None, "valid": None, "decided": None, "panel_decision": None}
    i = text.find("Summary Application Number")
    summ = text[i:] if i >= 0 else text
    la = summ.find(" Location Address ")
    pr = summ.find(" Proposal ", la if la >= 0 else 0)
    if la >= 0 and pr > la:
        out["address"] = summ[la + len(" Location Address "):pr].strip() or None
    if pr >= 0:
        at = summ.find(" Application Type ", pr)
        if at > pr:
            st = summ.rfind(" Status ", pr, at)
            if st > pr:
                out["proposal"] = summ[pr + len(" Proposal "):st].strip() or None
                out["status"] = summ[st + len(" Status "):at].strip() or None
            ad = summ.find(" Appeal Decision ", at)
            if ad > at:
                out["type"] = summ[at + len(" Application Type "):ad].strip() or None

    def d(label):
        m = re.search(re.escape(label) + r"\s+(\d{2}/\d{2}/\d{4})", summ)
        return _dmy(m.group(1)) if m else None
    out["received"] = d("Application Received Date")
    out["valid"] = d("Application Valid Date")
    out["decided"] = d("Decision Date")
    m = re.search(r"Committee Meeting\s+(.*?)\s+Decision\s+(.*?)\s+Decision Date", text)
    if m:
        out["panel_decision"] = m.group(2).strip()
    return out


_UNMAPPED: set = set()


def _warn_unmapped(label: str):
    if label not in _UNMAPPED:
        _UNMAPPED.add(label)
        print(f"    ⚠ UNMAPPED STATUS {label!r}: saved as 'pending' until a mapping is added")


def _word_status(s: str) -> Optional[str]:
    """Map one portal phrase to our vocabulary, or None if it says nothing decisive."""
    s = (s or "").strip().lower()
    if not s or s in ("n/a", "pending", "pending decision", "pending consideration"):
        return None
    if "withdraw" in s:
        return "withdrawn"
    if any(x in s for x in ("invalid", "returned", "closed", "cancel", "not proceed", "finally disposed",
                            "no further action", "not determined", "entered in error", "not valid")):
        return "closed"
    if "no objection" in s or "no formal objection" in s or "no observations" in s:
        return "approved"
    # Consultation replies on County Matters etc. ('Observations'): not a grant or a refusal.
    if "observations" in s:
        return "closed"
    # 'Prior Approval Required' = a full application is needed; it is NOT an approval
    # ('Prior Approval Not Required' is handled by the approval words below).
    if "approval required" in s and "not" not in s:
        return "closed"
    if "split" in s or "part consent" in s or "part permit" in s or "partially" in s:
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit", "not lawful", "unlawful")):
        return "refused"
    if any(x in s for x in ("approv", "grant", "permit", "allow", "consent", "not required",
                            "lawful", "agree", "accept", "discharge", "prior approval")):
        return "approved"
    return None


def normalise_status(results_status: str, detail_status: str, panel_decision: str, has_decision_date: bool) -> str:
    """Panel decision first (the outcome), then the detail Status, then the results-table status.
    'Decision Issued' alone is ambiguous, so it is only trusted via the panel."""
    # A withdrawal anywhere wins (one record showed results 'Withdrawn' with panel 'Not Required').
    if any("withdraw" in (x or "").lower() for x in (panel_decision, detail_status, results_status)):
        return "withdrawn"
    for src in (panel_decision, detail_status, results_status):
        r = _word_status(src)
        if r:
            return r
    if has_decision_date:  # decided, but nothing we could map
        label = f"{results_status} | {detail_status} | {panel_decision}"
        _warn_unmapped(label)
    return "pending"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _token(page: str) -> str:
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page) or \
        re.search(r'value="([^"]+)"[^>]*name="__RequestVerificationToken"', page)
    return m.group(1) if m else ""


async def _req(client: httpx.AsyncClient, method: str, path: str, **kw) -> httpx.Response:
    last = None
    for attempt in (1, 2, 3):
        try:
            r = await client.request(method, path if path.startswith("http") else BASE + path, **kw)
            if r.status_code >= 500 or r.status_code == 429:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r
        except Exception as e:
            last = e
            print(f"    ⚠ {method} {path} failed (attempt {attempt}): {e}")
            await asyncio.sleep(4 * attempt)
    raise RuntimeError(f"North Devon portal unavailable: {last}")


async def sweep(client: httpx.AsyncClient, kind: str, d_from: date, d_to: date, per_page: int = 50) -> dict:
    """kind 'received' (DateReceived*) or 'determined' (DateDetermined*). -> {ref: row dict}"""
    field = "DateReceived" if kind == "received" else "DateDetermined"
    client.cookies.clear()
    r = await _req(client, "GET", "/Search/Advanced")
    tok = _token(r.text)
    if not tok:
        raise RuntimeError("No anti-forgery token on /Search/Advanced")
    form = {"__RequestVerificationToken": tok, "AdvancedSearch": "True",
            "SearchPlanning": ["true", "false"], "SearchEnforcement": "false",
            "SearchAppeals": "false", "Outstanding": "false",
            field + "From": d_from.strftime("%d/%m/%Y"), field + "To": d_to.strftime("%d/%m/%Y")}
    r = await _req(client, "POST", "/Search/Results", data=form, headers={"Referer": BASE + "/Search/Advanced"})
    total, rows = parse_results(r.text)
    if per_page == 50:
        r = await _req(client, "POST", "/Search/ChangeResultsPerPage",
                       data={"__RequestVerificationToken": tok, "resultsPerPage": "50"},
                       headers={"Referer": BASE + "/Search/Results"})
        total2, rows = parse_results(r.text)
        total = total2 if total2 is not None else total
    out = {}
    for ref, loc, valid, status in rows:
        out[ref] = {"reference": ref, "location": loc, "valid_text": valid, "results_status": status}
    size = 50 if per_page == 50 else 10
    page_no = 1
    while total is not None and len(out) < total and page_no < 60:
        page_no += 1
        await asyncio.sleep(1)
        path = f"/Search/Results/{page_no}/50" if per_page == 50 else f"/Search/Results/{page_no}"
        r = await _req(client, "GET", path)
        _, rows = parse_results(r.text)
        new = 0
        for ref, loc, valid, status in rows:
            if ref not in out:
                new += 1
            out[ref] = {"reference": ref, "location": loc, "valid_text": valid, "results_status": status}
        if new == 0:
            break
    print(f"    ℹ {kind} sweep {d_from:%d/%m/%Y}-{d_to:%d/%m/%Y}: portal says {total}, collected {len(out)} "
          f"({'50' if per_page == 50 else '10'} per page, {page_no} page(s))")
    if total is not None and len(out) < total and per_page == 50:
        print(f"    ⚠ shortfall at 50/page - repeating the {kind} sweep at 10/page")
        await asyncio.sleep(1)
        return await sweep(client, kind, d_from, d_to, per_page=10)
    if total is not None and len(out) < total:
        print(f"    ⚠ {kind} sweep incomplete ({len(out)} of {total})")
    return out


# ---------------------------------------------------------------------------
# Supabase (same REST pattern as every other scraper here)
# ---------------------------------------------------------------------------
def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"}


async def _council_id() -> int:
    env = os.environ.get("NORTHDEVON_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name,system,coverage_source", "name": f"ilike.{COUNCIL_NAME_LIKE}"},
                        headers=_h())
        r.raise_for_status()
        rows = r.json()
    if len(rows) != 1:
        raise RuntimeError(f"Expected exactly one councils row matching North Devon, found {len(rows)}: {rows}. "
                           f"Set NORTHDEVON_COUNCIL_ID.")
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind North Devon scraper (portal HTML)")
    print(f"Window: {WINDOW_DAYS} days (+{LAG_DAYS} lag on received)   DRY_RUN: {DRY_RUN}   "
          f"detail delay {DETAIL_DELAY}s   max {MAX_MINUTES} min")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_KEY):
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    cid = None
    if SUPABASE_URL and SUPABASE_KEY:
        cid = await _council_id()
        print(f"[North Devon] (council_id={cid})")
    today = date.today()
    d_from = today - timedelta(days=WINDOW_DAYS)

    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"}) as client:
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
                r = await _req(client, "GET", f"/Planning/Display/{ref}")
                det = parse_detail_text(_strip(r.text))
            except Exception as e:
                failed += 1
                print(f"    ⚠ detail {ref} failed: {e}")
                continue
            if det["type"] and any(w in det["type"].lower() for w in SKIP_TYPE_WORDS):
                skipped += 1
                await asyncio.sleep(DETAIL_DELAY)
                continue
            address = det["address"] or row["location"]
            status = normalise_status(row["results_status"], det["status"] or "", det["panel_decision"] or "",
                                      bool(det["decided"]))
            valid = det["valid"] or _dmy(row["valid_text"], "-")
            submitted = valid or det["received"]
            apps.append({
                "reference": ref, "address": address, "postcode": _extract_postcode(address),
                "description": det["proposal"], "application_type": det["type"], "status": status,
                "submitted_date": submitted.isoformat() if submitted else None,
                "decision_date": det["decided"].isoformat() if (det["decided"] and status != "pending") else None,
                "council_url": f"{BASE}/Planning/Display/{ref}",
                "_combo": (row["results_status"], det["status"] or "", det["panel_decision"] or ""),
            })
            if n % 25 == 0:
                print(f"    ℹ detail pages {n}/{len(refs)}")
            await asyncio.sleep(DETAIL_DELAY)

    print(f"    ℹ parsed {len(apps)}; skipped {skipped} non-planning types; detail failures {failed}")
    print(f"    ℹ by status: {dict(Counter(a['status'] for a in apps))}")
    print("    ℹ results status | detail status | panel decision -> status (count):")
    for (combo, st), n in Counter((a["_combo"], a["status"]) for a in apps).most_common(30):
        print(f"        {combo[0]!r:<36} | {combo[1]!r:<22} | {combo[2]!r:<18} -> {st:<9} x{n}")
    print(f"    ℹ application types: {dict(Counter(a['application_type'] for a in apps).most_common(12))}")
    print(f"    ℹ missing proposal: {sum(1 for a in apps if not a['description'])}; "
          f"missing postcode: {sum(1 for a in apps if not a['postcode'])}")

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
    if ok and not timed_out:
        await _patch_council(cid, {"coverage_source": SOURCE, "last_scraped_at": now,
                                   "last_saved_at": now, "consecutive_empty_runs": 0, "active": True})
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
