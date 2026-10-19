#!/usr/bin/env python3
"""
PlanFind - Eastbourne Borough Council scraper (Civica W2 API behind lewes-eastbourne.gov.uk).

Built 2026-10-19 from two rounds of real recon on the UK runner (not assumed):

  * The planning search on www.lewes-eastbourne.gov.uk (a GOSS site behind a Cloudflare
    managed challenge) loads its data from an open Civica W2 JSON API on a separate host
    that did NOT challenge plain HTTP from the runner:
        POST https://gtest.lewes-eastbourne.gov.uk/civica/Resource/Civica/Handler.ashx/keyobject/pagedsearch
        {"refType":"APPPlanCase","fromRow":1,"toRow":500,
         "searchFields":{"ApplicationValidDateFrom":"05/10/2026","ApplicationValidDateTo":"19/10/2026"},
         "NoTotalRows":true}
    No cookies, no browser. Code lists (decision, application type) come from
        GET .../core/getpicklist?pickListName=urn:www.comino.com:picklists:classes::LocalGov2::APP::util::picklists::<List>
  * Date filters WORK (verified: every record fell inside the window):
        ApplicationValidDate, ReceivedDate, DecisionDate (all From/To, dd/mm/yyyy).
  * toRow up to 500 works.
  * Records carry the application number (KeyNo, e.g. 260421), description, address parts,
    postcode, Easting/Northing (British National Grid), applicant, agent, valid/received/
    decision dates, ApplicationStatus (text) and CODED fields (DecisionCode, application type).
  * The detail call adds nothing we use (only comment counts), so no per-application requests.
  * Both Eastbourne and Lewes share the website, but this API (APPPlanCase) returned only
    Eastbourne addresses in recon. The data does not say so explicitly; the first DRY_RUN log
    shows the postcodes/wards seen.

Two sweeps per run, merged by reference:
  1. Valid sweep   (Valid Date in the last WINDOW_DAYS days) -> new applications.
  2. Decided sweep (Decision Date in the last WINDOW_DAYS days) -> turns older 'pending' rows
     into approved/refused/withdrawn.

DRY_RUN=1 (the default for the first run) prints the code lists, status counts, unmapped
decisions and sample rows and WRITES NOTHING to the database.

HONEST LIMITATIONS:
  - Decision codes seen so far: ACC, WDR, ISS (meaning comes from the portal's own code list,
    which this script fetches and prints). Anything it cannot map is printed once per run
    ('UNMAPPED DECISION') and saved as 'pending' until a mapping is added.
  - submitted_date is the VALID date (falling back to the received date).
  - Coordinates come from the record's Easting/Northing (converted to WGS84 with pyproj); if
    those are missing, the postcode is geocoded; failing that, the council centre is used.
  - council_url is a search link by application number
    (https://www.lewes-eastbourne.gov.uk/article/2086?civica.query.KeyNo=<ref>). The URL
    pattern follows the portal's own weekly-list links but has not been clicked-through.
  - Non-planning case types (pre-application advice and similar) are skipped by matching words
    in the type description.

Env: SUPABASE_URL, SUPABASE_KEY (required)
     WINDOW_DAYS (default 14; use 60 for the first backfill)
     DRY_RUN ("1" = print only, write nothing)
     EASTBOURNE_COUNCIL_ID (optional; otherwise looked up by name)
"""
import asyncio
import os
import re
import ssl
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

COUNCIL_NAME_LIKE = "*Eastbourne*"
WWW = "https://www.lewes-eastbourne.gov.uk"
H = "https://gtest.lewes-eastbourne.gov.uk/civica/Resource/Civica/Handler.ashx"
API = H + "/keyobject/pagedsearch"
REF_TYPE = "APPPlanCase"
PICK = "urn:www.comino.com:picklists:classes::LocalGov2::APP::util::picklists::"
SOURCE = "eastbourne_scraper"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "14"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "500"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "6"))
PAGE_DELAY_SECONDS = float(os.environ.get("PAGE_DELAY_SECONDS", "2"))
DRY_RUN = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
START = time.monotonic()

SKIP_TYPE_WORDS = ("pre app", "pre-app", "preapp", "pre application", "pre-application",
                   "validation surgery", "entered in error", "house name",
                   "app type - enforcement", "app type - invalid", "enforcement appeal")

try:
    from pyproj import Transformer
    _BNG = Transformer.from_crs(27700, 4326, always_xy=True)
except Exception:  # pyproj missing -> fall back to postcode geocoding
    _BNG = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dmy(s) -> Optional[date]:
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.strptime(s.strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None
    if d < date(1990, 1, 1) or d > date.today() + timedelta(days=366):
        return None
    return d


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


_UNMAPPED: set[str] = set()


def _normalise_status(decision: Optional[str], app_status: Optional[str] = None) -> str:
    """Same vocabulary as every other scraper (pending/approved/refused/withdrawn/closed).
    `decision` is the portal's decision description (from its own code list);
    `app_status` is the free-text ApplicationStatus, used only when there is no decision.
    Eastbourne's 18 decision codes (seen 2026-10-19): Advert Approved, Approved conditionally,
    Standard advert approval, Approved unconditionally, Committee (Enforcement) - Notice issue
    Endorsed, Committee (Enforcement) - Officer Negotiation recommended, Discharged, Deferred,
    Issued, Not determined, County Council - No Objection, County Council - Objection,
    Prior Approval - not required, Permitted Development, Refused, Split Advert Decision,
    Split Tree Decision, Withdrawn."""
    s = (decision or "").strip().lower()
    if not s:
        a = (app_status or "").strip().lower()
        if "withdraw" in a:
            return "withdrawn"
        if any(x in a for x in ("invalid", "returned", "closed", "cancel")):
            return "closed"
        return "pending"
    if s in ("pending", "pending decision", "pending consideration") or s.startswith("deferred"):
        return "pending"
    if "withdraw" in s:
        return "withdrawn"
    # Enforcement committee outcomes are not planning decisions ('Notice issue Endorsed'
    # contains 'issue', so this must come before the approval words).
    if "enforcement" in s:
        return "closed"
    if any(x in s for x in ("invalid", "no further action", "not appropriate procedure",
                            "advised to apply", "returned", "closed", "cancel",
                            "entered in error", "not valid", "finally disposed",
                            "not determined")):
        return "closed"
    # 'County Council - No Objection' must be caught before the refusal words ('objection').
    if "no objection" in s or "no formal objection" in s:
        return "approved"
    if "split" in s or "part consent" in s or "part permit" in s or "partially" in s:
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit", "not lawful",
                            "unlawful", "objection")):
        return "refused"
    # 'Discharged' = a condition was discharged (the application succeeded).
    if any(x in s for x in ("approv", "grant", "permit", "allow", "consent",
                            "not required", "lawful", "agree", "accept", "issue",
                            "discharge", "prior approval")):
        return "approved"
    if s not in _UNMAPPED:
        _UNMAPPED.add(s)
        print(f"    ⚠ UNMAPPED DECISION {decision!r}: saved as 'pending' until a mapping is added")
    return "pending"


def _items(ko: dict) -> dict:
    return {i.get("FieldName"): (i.get("Value") or "").strip() for i in ko.get("Items", [])}


def _address(it: dict) -> str:
    names = ", ".join(x for x in (it.get("PremisesName"), it.get("OtherPremises"), it.get("HouseName")) if x)
    street = f"{it.get('HouseNo', '')} {it.get('AddressLine1', '')}".strip()
    parts = [names, street, it.get("AddressLine2"), it.get("AddressLine3"), it.get("Postcode")]
    out = ", ".join(p.strip() for p in parts if p and p.strip())
    return out or re.sub(r"\s+", " ", it.get("PremisesAddress", "")).strip()


def _bng_to_latlng(e, n):
    if not _BNG:
        return None
    try:
        ei, ni = int(float(str(e).strip())), int(float(str(n).strip()))
        if ei <= 0 or ni <= 0:
            return None
        lon, lat = _BNG.transform(ei, ni)
        if 49.5 < lat < 61 and -9 < lon < 2.5:
            return round(lat, 6), round(lon, 6)
    except Exception:
        pass
    return None


def _record(ko: dict, decisions: dict, types: dict) -> Optional[dict]:
    it = _items(ko)
    ref = (it.get("KeyNo") or ko.get("KeyText") or "").strip()
    if not ref:
        return None
    type_code = it.get("PlanningApplicationTypeCode", "")
    type_desc = types.get(type_code) or type_code
    if any(w in type_desc.lower() for w in SKIP_TYPE_WORDS):
        return {"_skip": True}
    dec_code = it.get("DecisionCode", "")
    dec_desc = decisions.get(dec_code) or dec_code
    status = _normalise_status(dec_desc, it.get("ApplicationStatus"))
    valid = _dmy(it.get("ApplicationValidDate"))
    received = _dmy(it.get("ReceivedDate"))
    decided = _dmy(it.get("DecisionDate"))
    submitted = valid or received
    address = _address(it)
    postcode = (it.get("Postcode") or "").upper() or _extract_postcode(address)
    ll = _bng_to_latlng(it.get("Easting"), it.get("Northing"))
    return {
        "reference": ref,
        "address": address,
        "postcode": postcode or None,
        "description": it.get("PlanningApplicationDescription") or None,
        "application_type": type_desc or None,
        "status": status,
        "submitted_date": submitted.isoformat() if submitted else None,
        "decision_date": decided.isoformat() if (decided and status != "pending") else None,
        "council_url": f"{WWW}/article/2086?civica.query.KeyNo={ref}",
        "lat": ll[0] if ll else None,
        "lng": ll[1] if ll else None,
        "_dec_code": dec_code, "_dec_desc": dec_desc, "_app_status": it.get("ApplicationStatus", ""),
    }


# ---------------------------------------------------------------------------
# API calls and sweeps
# ---------------------------------------------------------------------------
def _hdr():
    return {"Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest", "Referer": WWW + "/planning",
            "Origin": WWW, "User-Agent": UA}


async def _picklist(client: httpx.AsyncClient, name: str) -> dict:
    for attempt in (1, 2):
        try:
            r = await client.get(f"{H}/core/getpicklist?pickListName={PICK}{name}")
            r.raise_for_status()
            return {str(x.get("Code")): str(x.get("Desc")) for x in r.json()}
        except Exception as e:
            print(f"    ⚠ picklist {name} failed (attempt {attempt}): {e}")
            await asyncio.sleep(3)
    return {}


async def _fetch(client: httpx.AsyncClient, fields: dict, frm: int, to: int, total: bool) -> dict:
    body = {"refType": REF_TYPE, "fromRow": frm, "toRow": to,
            "searchFields": fields, "NoTotalRows": not total}
    for attempt in (1, 2, 3):
        try:
            r = await client.post(API, json=body)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"    ⚠ request failed (attempt {attempt}): {e}")
            await asyncio.sleep(5 * attempt)
    raise RuntimeError("Eastbourne API unavailable")


async def sweep(client, kind: str, d_from: date, d_to: date, decisions: dict, types: dict) -> dict[str, dict]:
    """kind 'valid' (ApplicationValidDate) or 'decided' (DecisionDate)."""
    fn = "ApplicationValidDate" if kind == "valid" else "DecisionDate"
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
            rec = _record(ko, decisions, types)
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
    return out


def merge(valid: dict[str, dict], decided: dict[str, dict]) -> list[dict]:
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
    env = os.environ.get("EASTBOURNE_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name,system,coverage_source", "name": f"ilike.{COUNCIL_NAME_LIKE}"},
                        headers=_h())
        r.raise_for_status()
        rows = r.json()
    if len(rows) != 1:
        raise RuntimeError(f"Expected exactly one councils row matching Eastbourne, found {len(rows)}: {rows}. "
                           f"Set EASTBOURNE_COUNCIL_ID, or run the check/insert SQL first.")
    print(f"    ℹ councils row: {rows[0]}")
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Eastbourne scraper (Civica W2 API)")
    print(f"Window:   {WINDOW_DAYS} days   Page size: {PAGE_SIZE}   DRY_RUN: {DRY_RUN}   pyproj: {bool(_BNG)}")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_KEY):
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    cid = None
    if SUPABASE_URL and SUPABASE_KEY:
        cid = await _council_id()
        print(f"[Eastbourne] (council_id={cid})")
    today = date.today()
    d_from = today - timedelta(days=WINDOW_DAYS)

    async with httpx.AsyncClient(timeout=60, verify=ssl.create_default_context(),
                                 follow_redirects=True, headers=_hdr()) as client:
        decisions = await _picklist(client, "PlanDecisions")
        types = await _picklist(client, "PlanApplicationTypes")
        print(f"    ℹ decision codes ({len(decisions)}): "
              + "; ".join(f"{k}={v}" for k, v in sorted(decisions.items())))
        print(f"    ℹ application types ({len(types)}): "
              + "; ".join(f"{k}={v}" for k, v in sorted(types.items())))
        await asyncio.sleep(1)
        valid = await sweep(client, "valid", d_from, today, decisions, types)
        decided = await sweep(client, "decided", d_from, today, decisions, types)

    apps = merge(valid, decided)
    only_decided = sum(1 for r in decided if r not in valid)
    print(f"    ℹ valid in window: {len(valid)}; decided in window: {len(decided)} "
          f"({only_decided} not in the valid list); total {len(apps)}")
    by_status = Counter(a["status"] for a in apps)
    print(f"    ℹ by status: {dict(by_status)}")
    combos = Counter((a["_dec_code"], a["_dec_desc"], a["status"]) for a in apps)
    print("    ℹ decision code -> description -> status (count):")
    for (code, desc, st), n in combos.most_common():
        print(f"        {code or '(none)':<6} {desc or '(none)':<45} -> {st:<9} x{n}")
    print(f"    ℹ application statuses: {dict(Counter(a['_app_status'] for a in apps).most_common(8))}")
    print(f"    ℹ with BNG coordinates: {sum(1 for a in apps if a.get('lat'))} of {len(apps)}")
    print(f"    ℹ postcode areas seen: {dict(Counter((a['postcode'] or '?').split(' ')[0] for a in apps).most_common(8))}")

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

    need = [a["postcode"] for a in apps if a.get("postcode") and not a.get("lat")]
    coords = await geocode(need) if need else {}
    print(f"    Geocoded {len(coords)} of {len(set(need))} postcodes (those without coordinates)")
    for a in apps:
        if not a.get("lat"):
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
