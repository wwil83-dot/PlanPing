#!/usr/bin/env python3
"""
PlanFind - Waverley Borough Council scraper (Civica W2 "planning360" portal).

Built 2026-10-19 from two rounds of real recon on the UK runner (not assumed):

  * The portal (https://planning360.waverley.gov.uk:4443/planning) loads its data from an
    open JSON API. Plain HTTP works - no browser, no login. Imperva sets a couple of cookies
    on the first page load, so we fetch the start page once and keep the cookie jar.
        POST {BASE}/w2webparts/Resource/Civica/Handler.ashx/keyobject/pagedsearch
        {"refType":"GFPlanning","fromRow":1,"toRow":500,
         "searchFields":{"SDate1From":"05/10/2026","SDate1To":"19/10/2026"},"NoTotalRows":true}
  * The portal is UK/Ireland only (the UK runner is fine).
  * toRow up to 500 works; row ranges page cleanly with no overlap; TotalRows is returned
    when NoTotalRows is false. Default order is newest first.
  * Date filters WORK (verified: every record returned fell inside the window):
        SDate1 = Date Valid, SDate3 = Decision Date, SDate5 = Date Received.
  * Each record carries: Case No (SDescription), Applicant, Agent, Application Address,
    Premises Address (comma separated, nicer), Proposal (properly cased), Date Valid,
    Date Received, Decision Date, Decision (SPicklist2: 'PENDING', 'NON MATERIAL AMENDMENT
    ALLOWED', ...), Application Type (SPicklist5), Ward, Parish, Postcode, UPRN.
  * The detail call adds nothing we use (only comment counts), so no per-application requests.
  * The weekly-list PDFs only list valid applications and have inconsistent file names, so
    they are not used.

Two sweeps per run, merged by reference:
  1. Valid sweep   (Date Valid in the last WINDOW_DAYS days) -> new applications, with their
     current decision if already decided.
  2. Decided sweep (Decision Date in the last WINDOW_DAYS days) -> turns older 'pending'
     rows into approved/refused whenever the application was validated.

HONEST LIMITATIONS:
  - Decision vocabulary: only a few values were seen in recon ('PENDING', 'NON MATERIAL
    AMENDMENT ALLOWED'). The mapping below covers the obvious wording; anything it does not
    recognise is printed once per run ('UNMAPPED DECISION') and saved as 'pending' until a
    mapping is added.
  - submitted_date is the DATE VALID (falling back to Date Received). Waverley sometimes
    validates weeks after receipt, and valid is when the application appears on the portal.
  - Applications received but not yet valid are not on the portal's valid-date search and
    are therefore not picked up until validated.
  - council_url is the portal deep link (#VIEW?RefType=GFPlanning&KeyNo=...). It works
    inside the UK; the hash route has not been clicked-through from outside the search page.
  - Non-planning case types (pre-application advice, validation surgery, entered in error,
    house name change, DC consultative forum) are skipped.

Env: SUPABASE_URL, SUPABASE_KEY (required)
     WINDOW_DAYS (default 14; use 60 for the first backfill)
     WAVERLEY_COUNCIL_ID (optional; otherwise looked up by name)
"""
import asyncio
import os
import re
import ssl
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

COUNCIL_NAME = "Waverley Borough Council"
BASE = "https://planning360.waverley.gov.uk:4443"
API = BASE + "/w2webparts/Resource/Civica/Handler.ashx/keyobject/pagedsearch"
SOURCE = "waverley_scraper"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "14"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "500"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "6"))
PAGE_DELAY_SECONDS = float(os.environ.get("PAGE_DELAY_SECONDS", "2"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
START = time.monotonic()

# Case types that are not planning applications the public wants to see.
SKIP_TYPE_WORDS = ("pre app", "pre-app", "validation surgery", "entered in error",
                   "house name change", "development control consultative forum")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dmy(s) -> Optional[date]:
    """'16/10/2026' (or '16/10/2026 14:00:06') -> date; rejects junk."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.strptime(s.strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None
    if d < date(2000, 1, 1) or d > date.today() + timedelta(days=366):
        return None
    return d


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _tidy_type(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    return s.title() if s.isupper() else s


_UNMAPPED: set[str] = set()


def _normalise_status(decision: Optional[str]) -> str:
    """Same vocabulary as every other scraper (pending/approved/refused/withdrawn/closed)."""
    s = (decision or "").strip().lower()
    if not s or s in ("pending", "pending decision", "pending consideration"):
        return "pending"
    if "withdraw" in s:
        return "withdrawn"
    if any(x in s for x in ("invalid", "no further action", "not appropriate procedure",
                            "advised to apply", "returned", "closed", "acknod",
                            "entered in error")):
        return "closed"
    # Part-granted / part-refused: some consent was given (judgement call, as elsewhere).
    if "split" in s or "part consent" in s or "part permit" in s or "partially" in s:
        return "approved"
    # Refusals first, so 'Advertisement Consent Refused' / 'not lawful' are not caught by
    # the approval words below.
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit", "not lawful",
                            "unlawful", "objection raised", "prior approval required and refused")):
        return "refused"
    if any(x in s for x in ("approv", "grant", "permit", "allow", "consent", "no objection",
                            "not required", "lawful", "agree", "full permission",
                            "prior approval")):
        return "approved"
    if s not in _UNMAPPED:
        _UNMAPPED.add(s)
        print(f"    ⚠ UNMAPPED DECISION {decision!r}: saved as 'pending' until a mapping is added")
    return "pending"


def _items(ko: dict) -> dict:
    return {i.get("FieldName"): (i.get("Value") or "") for i in ko.get("Items", [])}


def _record(ko: dict) -> Optional[dict]:
    it = _items(ko)
    ref = (it.get("SDescription") or "").strip()
    if not ref:
        return None
    app_type_raw = (it.get("SPicklist5") or it.get("SPicklist1") or "").strip()
    if any(w in app_type_raw.lower() for w in SKIP_TYPE_WORDS) or \
            any(w in (it.get("SPicklist1") or "").lower() for w in SKIP_TYPE_WORDS):
        return {"_skip": True}
    decision = (it.get("SPicklist2") or "").strip()
    status = _normalise_status(decision)
    valid = _dmy(it.get("SDate1"))
    received = _dmy(it.get("SDate5"))
    decided = _dmy(it.get("SDate3"))
    submitted = valid or received
    address = (it.get("Address") or "").strip() or (it.get("SText9") or "").strip().title()
    postcode = (it.get("AText6") or "").strip().upper() or _extract_postcode(address)
    key = ko.get("KeyNumber")
    url = (f"{BASE}/planning/search-applications#VIEW?RefType=GFPlanning&KeyNo={key}&KeyText=Subject"
           if key else f"{BASE}/planning")
    return {
        "reference": ref,
        "address": address,
        "postcode": postcode or None,
        "description": (it.get("Proposal") or it.get("SText10") or "").strip() or None,
        "application_type": _tidy_type(app_type_raw),
        "status": status,
        "submitted_date": submitted.isoformat() if submitted else None,
        "decision_date": decided.isoformat() if (decided and status != "pending") else None,
        "council_url": url,
    }


# ---------------------------------------------------------------------------
# The two sweeps
# ---------------------------------------------------------------------------
def _ssl_ctx():
    # Use the operating system's certificate store (the portal is a public-CA cert on a
    # non-standard port; the recon used the system store successfully).
    return ssl.create_default_context()


async def _fetch(client: httpx.AsyncClient, fields: dict, frm: int, to: int, total: bool) -> dict:
    body = {"refType": "GFPlanning", "fromRow": frm, "toRow": to,
            "searchFields": fields, "NoTotalRows": not total}
    for attempt in (1, 2, 3):
        try:
            r = await client.post(API, json=body)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"    ⚠ request failed (attempt {attempt}): {e}")
            await asyncio.sleep(5 * attempt)
    raise RuntimeError("Waverley API unavailable")


async def sweep(client: httpx.AsyncClient, kind: str, d_from: date, d_to: date) -> dict[str, dict]:
    """kind 'valid' (SDate1) or 'decided' (SDate3)."""
    fn = "SDate1" if kind == "valid" else "SDate3"
    fields = {f"{fn}From": d_from.strftime("%d/%m/%Y"), f"{fn}To": d_to.strftime("%d/%m/%Y")}
    out: dict[str, dict] = {}
    skipped = 0
    total_rows = None
    for page_no in range(MAX_PAGES):
        frm = page_no * PAGE_SIZE + 1
        data = await _fetch(client, fields, frm, frm + PAGE_SIZE - 1, total=(page_no == 0))
        if page_no == 0:
            total_rows = data.get("TotalRows")
        kos = data.get("KeyObjects") or []
        for ko in kos:
            rec = _record(ko)
            if not rec:
                continue
            if rec.get("_skip"):
                skipped += 1
                continue
            out[rec["reference"]] = rec
        print(f"    ℹ {kind} sweep page {page_no + 1}: {len(kos)} read, {len(out)} kept so far"
              + (f" (portal says {total_rows} in window)" if page_no == 0 and total_rows is not None else ""))
        if len(kos) < PAGE_SIZE:
            break
        if page_no == MAX_PAGES - 1:
            print(f"    ⚠ {kind} sweep hit MAX_PAGES={MAX_PAGES}; raise it for a longer backfill")
        await asyncio.sleep(PAGE_DELAY_SECONDS)
    if skipped:
        print(f"    ℹ {kind} sweep skipped {skipped} non-planning case types")
    if total_rows is not None and isinstance(total_rows, int) and total_rows > MAX_PAGES * PAGE_SIZE:
        print(f"    ⚠ {kind} sweep: portal reports {total_rows} rows, more than MAX_PAGES x PAGE_SIZE")
    return out


def merge(valid: dict[str, dict], decided: dict[str, dict]) -> list[dict]:
    """A decided-sweep record carries the real decision, so it wins on status and
    decision_date; everything else comes from whichever sweep saw it first."""
    merged = dict(valid)
    for ref, rec in decided.items():
        if ref in merged:
            merged[ref]["status"] = rec["status"]
            merged[ref]["decision_date"] = rec["decision_date"]
        else:
            merged[ref] = rec
    return list(merged.values())


# ---------------------------------------------------------------------------
# Supabase (same REST pattern as every other scraper here)
# ---------------------------------------------------------------------------
def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"}


async def _council_id() -> int:
    env = os.environ.get("WAVERLEY_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name", "name": f"eq.{COUNCIL_NAME}"}, headers=_h())
        r.raise_for_status()
        rows = r.json()
    if not rows:
        raise RuntimeError(f"No councils row named {COUNCIL_NAME!r}.")
    return rows[0]["id"]


async def _upsert(records: list[dict]) -> bool:
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


async def geocode(postcodes: list[str]) -> dict:
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Waverley scraper (Civica W2 API)")
    print(f"Window:   {WINDOW_DAYS} days   Page size: {PAGE_SIZE}")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    cid = await _council_id()
    print(f"[{COUNCIL_NAME}] (council_id={cid})")
    today = date.today()
    d_from = today - timedelta(days=WINDOW_DAYS)

    async with httpx.AsyncClient(timeout=60, verify=_ssl_ctx(), follow_redirects=True, headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE + "/planning/search-applications",
            "User-Agent": UA}) as client:
        # One page load first so the Imperva cookies are in the jar (as in the recon).
        try:
            r = await client.get(BASE + "/planning", headers={"Accept": "text/html"})
            print(f"    ℹ start page {r.status_code}, cookies: {[c.name for c in client.cookies.jar]}")
        except Exception as e:
            print(f"    ⚠ start page failed ({e}); trying the API anyway")
        await asyncio.sleep(1)
        valid = await sweep(client, "valid", d_from, today)
        decided = await sweep(client, "decided", d_from, today)

    apps = merge(valid, decided)
    only_decided = sum(1 for r in decided if r not in valid)
    print(f"    ℹ valid in window: {len(valid)}; decided in window: {len(decided)} "
          f"({only_decided} not in the valid list); total {len(apps)}")
    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    print(f"    ℹ by status: {by_status}")

    if not apps:
        print("    ✗ nothing found - not saving")
        await _patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        return

    need = [a["postcode"] for a in apps if a.get("postcode")]
    coords = await geocode(need) if need else {}
    print(f"    Geocoded {len(coords)} of {len(set(need))} postcodes")
    for a in apps:
        pc = (a.get("postcode") or "").strip().upper().replace(" ", "")
        if pc in coords:
            a["lat"], a["lng"] = coords[pc]
    geocoded = [(a["lat"], a["lng"]) for a in apps if a.get("lat")]
    if geocoded:
        clat = statistics.median(g[0] for g in geocoded)
        clng = statistics.median(g[1] for g in geocoded)
        n = 0
        for a in apps:
            if not a.get("lat"):
                a["lat"], a["lng"], a["geocode_quality"] = clat, clng, "centroid"
                n += 1
        if n:
            print(f"    Council centroid fallback for {n} apps")

    records = [{
        "council_id": cid,
        "reference": a["reference"],
        "address": a["address"],
        "postcode": a["postcode"],
        "lat": a.get("lat"),
        "lng": a.get("lng"),
        "description": a["description"],
        "application_type": a["application_type"],
        "status": a["status"],
        "submitted_date": a["submitted_date"],
        "decision_date": a["decision_date"],
        "council_url": a["council_url"],
        "source": SOURCE,
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
