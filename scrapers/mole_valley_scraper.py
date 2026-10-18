#!/usr/bin/env python3
"""
PlanFind — Mole Valley District Council scraper (Statmap "Horizon" public portal).

Built 2026-10-18 from three rounds of real recon on the UK runner (not assumed):

  * The portal is a single-page app over an OPEN JSON API. No login, no cookies,
    no browser, no WAF challenge:
        POST https://molevalley-publicportal.statmap.co.uk/horizoNext/api/publicportal/planningApplications/pageRequest
    The body is the one the page itself sends (see _body()). Each record has:
        name (the application reference, e.g. MO/2026/01496), initialAppRef,
        address (lines separated by newlines, ends with the postcode),
        proposal, receivedDate, status ("Live"/"Determined"), decision
        ("PENDING"/"APPROVE WITH CONDITIONS"/"REFUSE"/...), decisionDate,
        applicationTypeId_relatedRecord.name.
  * pageSize 500 works.
  * order {"receivedDate": "desc"} and order {"decisionDate": "desc"} both work.
  * Date FILTERS do NOT work: every filter shape we tried was silently ignored
    (total stayed 123,450). So we do not filter: we read newest-first and stop
    as soon as we pass the cutoff date.
  * Bad data exists: e.g. LEA/22692 has receivedDate 2202-02-02 and decisionDate
    1971. Any date in the future (or before 2000) is ignored.
  * The portal's "Weekly Lists" feature is abandoned (5 files, last 2026-05-29)
    so is not used.

Two sweeps per run, merged by reference:
  1. Received sweep  (newest received first) -> every application received in the
     last WINDOW_DAYS days, with its current decision if it already has one.
  2. Decided sweep   (newest decided first)  -> every application decided in the
     last WINDOW_DAYS days, whenever it was received. This is what turns older
     "pending" rows into approved/refused.

HONEST LIMITATIONS:
  - The list gives the RECEIVED date, not the validated date (the detail
    endpoint GET .../planningApplications/{id} has validDate and easting/
    northing, but fetching it per application is not worth the extra requests).
    submitted_date is therefore the received date.
  - council_url is the portal's landing page: the per-application URL of the
    single-page app has not been confirmed.
  - Decision vocabulary beyond APPROVE*/REFUS*/WITHDRAWN/PENDING is unconfirmed.
    Any decision text the mapping doesn't recognise is printed once per run
    ("UNMAPPED DECISION") and saved as 'pending' until a mapping is added.

Env: SUPABASE_URL, SUPABASE_KEY (required)
     WINDOW_DAYS (default 14; use 60 for the first backfill)
     MOLE_VALLEY_COUNCIL_ID (optional; otherwise looked up by name)
"""
import asyncio
import os
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

COUNCIL_NAME = "Mole Valley District Council"
PORTAL_URL = "https://molevalley-publicportal.statmap.co.uk/horizonext"
API_URL = ("https://molevalley-publicportal.statmap.co.uk/horizoNext/api/"
           "publicportal/planningApplications/pageRequest")
SOURCE = "mole_valley_scraper"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "14"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "500"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "6"))           # per sweep; safety stop
PAGE_DELAY_SECONDS = float(os.environ.get("PAGE_DELAY_SECONDS", "2"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SELECT = {"name": True, "initialAppRef": True, "address": True, "proposal": True,
          "receivedDate": True, "status": True, "decision": True, "id": True}

START = time.monotonic()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _body(order: dict, offset: int) -> dict:
    return {
        "select": SELECT,
        "pageSize": PAGE_SIZE,
        "offset": offset,
        "filter": {"parts": [{"filterItems": [
            {"columnName": "searchType", "value": "Simple", "operator": "="}]}]},
        "order": order,
        "advancedFilter": {"parts": []},
        "entityName": "P_APPLICATION",
    }


def _iso_date(s) -> Optional[date]:
    """'2026-10-13T11:55:03' -> date. Rejects junk: before 2000 or after tomorrow."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    if d < date(2000, 1, 1) or d > date.today() + timedelta(days=1):
        return None
    return d


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(addr: str) -> str:
    parts = [p.strip() for p in (addr or "").replace("\r", "").split("\n")]
    return ", ".join(p for p in parts if p)


_UNMAPPED: set[str] = set()


def _normalise_status(decision: Optional[str]) -> str:
    """Same vocabulary as every other scraper. The portal's own `status`
    ('Live'/'Determined') is a workflow stage, so only `decision` is used."""
    s = (decision or "").strip().lower()
    if not s or s == "pending":
        return "pending"
    if "withdraw" in s:
        return "withdrawn"
    # Outcomes that are not a grant/refusal of permission. Seen in the first
    # production run (2026-10-18): 'TPO CONFIRMED', 'NO FURTHER ACTION',
    # 'ENVIRONMENTAL IMPACT ASSESSMENT - REQUIRED'. Shown as 'closed' (same
    # neutral grey as withdrawn) rather than left as 'pending'.
    if any(x in s for x in ("closed", "no further action", "tpo confirmed", "environmental impact")):
        return "closed"
    # 'SPLIT DECISION (TREE OR CONDITIONS)' = part granted, part refused.
    # Treated as approved (some consent was given); a judgement call.
    if "split decision" in s:
        return "approved"
    if any(x in s for x in ("approv", "grant", "permit", "allow", "no objection", "not required")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit")):
        return "refused"
    if s not in _UNMAPPED:
        _UNMAPPED.add(s)
        print(f"    ⚠ UNMAPPED DECISION {decision!r}: saved as 'pending' until a mapping is added")
    return "pending"


def _record(r: dict) -> Optional[dict]:
    ref = (r.get("name") or "").strip()
    if not ref:
        return None
    received = _iso_date(r.get("receivedDate"))
    decision = (r.get("decision") or "").strip()
    status = _normalise_status(decision)
    decided = _iso_date(r.get("decisionDate")) if status != "pending" else None
    address = _clean_address(r.get("address") or "")
    return {
        "reference": ref,
        "address": address,
        "postcode": _extract_postcode(address),
        "description": (r.get("proposal") or "").strip() or None,
        "application_type": ((r.get("applicationTypeId_relatedRecord") or {}).get("name")) or None,
        "status": status,
        "submitted_date": received.isoformat() if received else None,
        "decision_date": decided.isoformat() if decided else None,
        "council_url": PORTAL_URL,
        "_received": received,
        "_decided_raw": _iso_date(r.get("decisionDate")),
    }


# ---------------------------------------------------------------------------
# The two sweeps
# ---------------------------------------------------------------------------
async def _fetch(client: httpx.AsyncClient, order: dict, offset: int) -> list[dict]:
    for attempt in (1, 2):
        try:
            r = await client.post(API_URL, json=_body(order, offset))
            r.raise_for_status()
            return r.json().get("records", [])
        except Exception as e:
            print(f"    ⚠ request failed (attempt {attempt}): {e}")
            await asyncio.sleep(5)
    raise RuntimeError("Mole Valley API unavailable")


async def sweep(client: httpx.AsyncClient, kind: str, cutoff: date) -> dict[str, dict]:
    """kind 'received' or 'decided'. Reads newest-first and stops once a valid
    date falls before `cutoff`. Junk-dated records are skipped, not treated as
    the end of the list."""
    field = "receivedDate" if kind == "received" else "decisionDate"
    out: dict[str, dict] = {}
    skipped_junk = 0
    for page_no in range(MAX_PAGES):
        recs = await _fetch(client, {field: "desc"}, page_no * PAGE_SIZE)
        if not recs:
            break
        reached_end = False
        for raw in recs:
            rec = _record(raw)
            if not rec:
                continue
            d = rec["_received"] if kind == "received" else rec["_decided_raw"]
            if d is None:
                skipped_junk += 1
                continue
            if d < cutoff:
                reached_end = True
                break
            out[rec["reference"]] = rec
        print(f"    ℹ {kind} sweep page {page_no + 1}: {len(recs)} read, {len(out)} in window so far")
        if reached_end:
            break
        if page_no == MAX_PAGES - 1:
            print(f"    ⚠ {kind} sweep hit MAX_PAGES={MAX_PAGES} before reaching {cutoff}; "
                  f"raise MAX_PAGES if this is a long backfill")
        await asyncio.sleep(PAGE_DELAY_SECONDS)
    if skipped_junk:
        print(f"    ℹ {kind} sweep ignored {skipped_junk} records with missing/impossible dates")
    return out


def merge(received: dict[str, dict], decided: dict[str, dict]) -> list[dict]:
    """A decided-sweep record carries the real decision, so it wins on status and
    decision_date; everything else comes from whichever sweep saw it first."""
    merged = dict(received)
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
    env = os.environ.get("MOLE_VALLEY_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name", "name": f"eq.{COUNCIL_NAME}"}, headers=_h())
        r.raise_for_status()
        rows = r.json()
    if not rows:
        raise RuntimeError(f"No councils row named {COUNCIL_NAME!r}. Run the INSERT SQL first.")
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Mole Valley scraper (Statmap API)")
    print(f"Window:   {WINDOW_DAYS} days   Page size: {PAGE_SIZE}")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    cid = await _council_id()
    print(f"[{COUNCIL_NAME}] (council_id={cid})")
    cutoff = date.today() - timedelta(days=WINDOW_DAYS)

    async with httpx.AsyncClient(timeout=60, headers={
            "Accept": "application/json, text/plain, */*", "User-Agent": UA}) as client:
        received = await sweep(client, "received", cutoff)
        decided = await sweep(client, "decided", cutoff)

    apps = merge(received, decided)
    new_only_decided = sum(1 for r in decided if r not in received)
    print(f"    ℹ received in window: {len(received)}; decided in window: {len(decided)} "
          f"({new_only_decided} not in the received list); total {len(apps)}")
    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    print(f"    ℹ by status: {by_status}")

    if not apps:
        print("    ✗ nothing found — not saving")
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
