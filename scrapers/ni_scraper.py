#!/usr/bin/env python3
"""
PlanFind — Northern Ireland shared Planning Portal scraper.

Built 2026-08-17 from real recon evidence — a live user session's
DevTools Network tab, not a guess. Every URL, header, and JSON field
name below was confirmed directly against
https://api-planningregister-planningportal.pr.tqinfra.co.uk before
this file was written. See recon/ni_planningsystemni_recon.py for the
exploratory tool that would have gathered this if a live user session
hadn't already surfaced it faster.

ARCHITECTURE — genuinely different from every other scraper in this
project, and simpler:
  - The site itself (planningregister.planningsystemni.gov.uk) is a
    client-side-rendered SPA — CONFIRMED by direct fetch: the raw HTML
    is just an empty app shell ("You need to enable JavaScript..."),
    no data anywhere in it.
  - BUT the SPA's real data comes from a clean, separate REST API on a
    different host: api-planningregister-planningportal.pr.tqinfra.co.uk
    (note: NOT the same domain as the page — a TerraQuest-hosted API,
    "tq-tenant" header identifies this as the NI tenant specifically).
  - CONFIRMED: this API needs NO authentication, session, or cookie at
    all — a real captured request showed "credentials: omit" and no
    Authorization header. Only two headers matter:
    "accept: application/json" and a fixed "tq-tenant" GUID (see
    NI_TQ_TENANT below). This means a plain httpx client is enough —
    NO PLAYWRIGHT / NO BROWSER NEEDED for this platform, unlike
    Idox/Arcus/Civica/Northgate. Meaningfully lighter and faster.
  - CONFIRMED: filtering by DistrictElectoralAreaId/deas is NOT
    required — a request with only AuthorityId (and no DEA params at
    all) returns the full, correct result set for that council. This
    matters a lot: NI's councils have dozens of DEAs between them, and
    looping through each would have made this far slower than the
    one-request-per-council-per-page approach used here.
  - One shared portal, 10 real district councils (AuthorityId 1-10 —
    see ni_councils.py for the confirmed real mapping). AuthorityId=11
    is NI's regional Department for Infrastructure, not a council —
    deliberately excluded, see ni_councils.py's docstring.

HONEST LIMITATIONS in this v1, worth remembering:
  - Every real search performed during recon used SearchStatus=valid.
    The API almost certainly also accepts SearchStatus=decided (it's a
    real radio-button option on the site's own form, "Show
    applications: Valid / Decided") but that was NEVER actually tested
    — no real evidence of what a decided application's JSON looks like,
    what decisionDate's format is once populated, or what
    applicationStatus text appears for an approved/refused outcome.
    This v1 queries SearchStatus=valid only. A real decided-status
    recheck pass (mirroring idox_scraper.py's pending_recheck
    mechanism) is real future work, not guessed here.
  - _normalise_ni_status()'s keyword matching is reused directly from
    idox_scraper.py's proven _normalise_status() — but it was only
    ever tested here against the real status strings actually observed
    during recon ("Consultation Open", "Valid", "Site Inspection
    Complete" — all correctly falling through to "pending", which is
    honest since none are a real decided outcome). Any genuinely new
    status text this normaliser hasn't seen before triggers a one-time
    diagnostic print (see _STATUS_DIAGNOSED below) rather than silently
    miscategorizing it — the same discipline as every WAF/decision-date
    diagnostic elsewhere in this project.
  - No application_type field exists anywhere in the API response.
    Deliberately left as None here rather than guessed — main.py's own
    _type_badge()/_is_major() already fall back to the reference
    number's final "/" segment (e.g. "/OUT", "/LBC", "/DCA") when
    application_type is blank, the same mechanism that already covers
    Idox councils with missing type data. NI's reference suffixes were
    observed to follow a similar shape (F, LBC, DC, DCA, A seen in real
    recon data) but the FULL real suffix vocabulary was never
    confirmed — this is exactly what that existing fallback exists for.
  - dateFrom/dateTo are sent as UTC midnight ISO timestamps here. A
    real captured request showed the front-end sending BST-adjusted
    23:00Z timestamps instead (i.e. local midnight converted to UTC) —
    this is very likely just what its date picker happened to produce,
    not a strict API requirement, but that's an assumption, not
    confirmed. If a real run shows systematically off-by-one-day
    results, this is the first place to check.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "20"))  # generous — this is a
                                                            # lightweight REST API,
                                                            # not browser automation
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "3"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "14"))
PAGE_SIZE    = 100   # CONFIRMED real API ceiling — a real request with
                      # PageSize=200 was rejected: "'Page Size' must be
                      # between 1 and 100."
MAX_PAGES    = 10    # safety cap — 1000 results/council/run ceiling

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------------------
# NI Planning Portal API — CONFIRMED real endpoint, headers, and params
# ---------------------------------------------------------------------------
NI_API_BASE = "https://api-planningregister-planningportal.pr.tqinfra.co.uk/api/v1"
NI_TQ_TENANT = "cfb86436-414d-4459-9545-93eec37615a2"  # CONFIRMED real header
                                                          # value, identifies the
                                                          # NI tenant specifically
NI_REFERER = "https://planningregister.planningsystemni.gov.uk/"


def _ni_headers() -> dict:
    return {
        "accept": "application/json, text/plain, */*",
        "tq-tenant": NI_TQ_TENANT,
        "referer": NI_REFERER,
    }


# ---------------------------------------------------------------------------
# Helpers — reused directly from idox_scraper.py where the logic is
# platform-agnostic (status normalisation, postcode extraction)
# ---------------------------------------------------------------------------
def _normalise_ni_status(s: str) -> str:
    if not s:
        return "pending"
    s = s.lower()
    if any(x in s for x in ("approv", "grant", "permit", "allow", "no objection")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
    return "pending"


_STATUS_DIAGNOSED: set[str] = set()


def _diagnose_unrecognised_status(raw_status: str, council_name: str):
    """Real evidence, not a guess — every status string this normaliser
    hasn't seen before during recon prints once, so a real run surfaces
    genuinely new NI-specific status text (e.g. a real decided outcome,
    since SearchStatus=decided was never tested) rather than silently
    filing it under 'pending' forever."""
    key = (raw_status or "").strip().lower()
    if not key or key in _STATUS_DIAGNOSED:
        return
    known_substrings = ("approv", "grant", "permit", "allow", "no objection",
                         "refus", "reject", "dismiss", "not permit", "withdraw",
                         "consultation open", "valid", "site inspection")
    if not any(k in key for k in known_substrings):
        _STATUS_DIAGNOSED.add(key)
        print(f"    ⚠ [{council_name}] STATUS DIAGNOSTIC: unrecognised "
              f"applicationStatus {raw_status!r} — filed as 'pending', "
              f"worth checking whether this is a real decided outcome "
              f"never seen during recon")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _date_only(s: Optional[str]) -> Optional[str]:
    """NI's API returns full ISO datetimes (e.g. '2026-08-04T10:34:00')
    — CONFIRMED real format from recon. Just need the date part."""
    if not s:
        return None
    return str(s).strip().split("T")[0]


# ---------------------------------------------------------------------------
# Supabase REST API — identical to every other scraper in this project
# ---------------------------------------------------------------------------
def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_h())
        r.raise_for_status()
        return r.json()


async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                json=records, headers=headers,
            )
            if r.status_code not in (200, 201, 204):
                cid_hint = records[0].get("council_id") if records else "?"
                print(f"    ✗ Upsert HTTP {r.status_code} (council_id={cid_hint}): {r.text[:300]}")
                return False
            return True
    except Exception as e:
        cid_hint = records[0].get("council_id") if records else "?"
        print(f"    ✗ Upsert exception (council_id={cid_hint}): {e}")
        return False


async def _supa_patch_council(council_id: int, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.patch(
            f"{SUPABASE_URL}/rest/v1/councils",
            params={"id": f"eq.{council_id}"},
            json=data,
            headers={**_h(), "Prefer": "return=minimal"},
        )


async def _supa_increment_empty_runs(council_id: int):
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            await c.post(
                f"{SUPABASE_URL}/rest/v1/rpc/increment_empty_runs",
                json={"council_id_param": council_id},
                headers={**_h(), "Prefer": "return=minimal"},
            )
        except Exception as e:
            print(f"    ⚠ Failed to increment empty-run counter (council_id={council_id}): {e}")


# ---------------------------------------------------------------------------
# Geocoding — identical to every other scraper in this project
# ---------------------------------------------------------------------------
async def geocode(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    if not unique:
        return results
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(unique), 100):
            try:
                r = await c.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": unique[i:i + 100]},
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"],
                        )
            except Exception as e:
                print(f"    ⚠ Geocoding batch failed ({len(unique[i:i + 100])} postcodes): {e}")
    return results


# ---------------------------------------------------------------------------
# NI Portal — one direct REST client per council, no browser required
# ---------------------------------------------------------------------------
class NIPortal:
    def __init__(self, council_name: str, authority_id: int, db_council_id: int):
        self.council_name = council_name
        self.authority_id = authority_id
        self.db_council_id = db_council_id  # immutable, set once, same
                                              # discipline as idox_scraper.py's
                                              # IdoxPortal — see its _log()
                                              # docstring for why this matters

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def scrape(self, client: httpx.AsyncClient, days_back: int) -> list[dict]:
        date_to = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        date_from = date_to - timedelta(days=days_back)

        all_apps: list[dict] = []
        page = 1
        while page <= MAX_PAGES:
            if should_stop():
                self._log(f"⚠ Time budget reached mid-council, keeping "
                           f"{len(all_apps)} already collected")
                break

            params = {
                "SearchStatus": "valid",
                "AuthorityId": self.authority_id,
                "authorities": self.authority_id,
                "DisplayType": "monthly",
                "PageSize": PAGE_SIZE,
                "PageNumber": page,
                "dateFrom": date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "dateTo": date_to.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "sortByDescending": "true",
                # CONFIRMED: no DistrictElectoralAreaId/deas param needed —
                # a real request without them returned the full, correct
                # result set for the council. See module docstring.
            }

            try:
                r = await client.get(f"{NI_API_BASE}/applications/list",
                                      params=params, headers=_ni_headers(), timeout=30)
            except Exception as e:
                self._log(f"⚠ Request error on page {page}: {e}")
                break

            if r.status_code != 200:
                self._log(f"⚠ HTTP {r.status_code} on page {page}: {r.text[:300]}")
                break

            try:
                data = r.json()
            except Exception as e:
                self._log(f"⚠ Non-JSON response on page {page}: {e}")
                break

            groups = (data.get("groups") or {}).get("items") or []
            page_apps: list[dict] = []
            for group in groups:
                page_apps.extend(group.get("applications") or [])

            self._log(f"Page {page}: {len(page_apps)} results across "
                      f"{len(groups)} month-group(s)")

            all_apps.extend(page_apps)

            if len(page_apps) < PAGE_SIZE:
                break  # last page — fewer results than we asked for
            page += 1

        return all_apps


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def process_council(portal: NIPortal, client: httpx.AsyncClient,
                           sem: asyncio.Semaphore, days_back: int) -> int:
    async with sem:
        cid = portal.db_council_id
        print(f"\n[{portal.council_name}] (council_id={cid})")

        if should_stop():
            print(f"    [{portal.council_name}] — skipping, time budget reached "
                  f"({elapsed_minutes():.1f} min elapsed)")
            return "TIME_BUDGET_SKIP"

        try:
            raw_apps = await portal.scrape(client, days_back)
        except Exception as e:
            print(f"    [{portal.council_name}] ✗ Error: {e}")
            return 0

        if not raw_apps:
            await _supa_patch_council(cid, {
                "last_scraped_at": datetime.now(timezone.utc).isoformat()
            })
            await _supa_increment_empty_runs(cid)
            return 0

        apps = []
        for a in raw_apps:
            raw_status = a.get("applicationStatus", "")
            _diagnose_unrecognised_status(raw_status, portal.council_name)
            address = a.get("siteAddress", "")
            apps.append({
                "reference":        a.get("applicationReferenceNumber"),
                "address":          address,
                "postcode":         _extract_postcode(address),
                "description":      a.get("proposalText"),
                "application_type": None,  # see module docstring — relies on
                                             # main.py's reference-suffix fallback
                "status":           _normalise_ni_status(raw_status),
                "submitted_date":   _date_only(a.get("dateReceived")),
                "decision_date":    _date_only(a.get("decisionDate")),
                "council_url":      NI_REFERER,  # no confirmed per-application
                                                   # detail URL yet — see recon
                                                   # step 4, not completed
            })

        # Deduplicate by reference — same defensive pattern as every
        # other scraper here, in case pagination ever overlaps
        seen: set[str] = set()
        unique_apps = []
        for a in apps:
            if a["reference"] and a["reference"] not in seen:
                seen.add(a["reference"])
                unique_apps.append(a)
        apps = unique_apps

        need = [a["postcode"] for a in apps if a.get("postcode")]
        if need:
            print(f"    [{portal.council_name}] Geocoding {len(set(need))} postcodes…")
            coords = await geocode(need)
            for app in apps:
                if app.get("postcode"):
                    pc = app["postcode"].strip().upper().replace(" ", "")
                    if pc in coords:
                        app["lat"], app["lng"] = coords[pc]

        # Council centroid fallback for anything still ungeocoded — same
        # pattern as idox_scraper.py, keeps major applications visible
        # on the map even without a matched postcode
        geocoded = [(a.get("lat"), a.get("lng")) for a in apps if a.get("lat") and a.get("lng")]
        if geocoded:
            import statistics
            centroid_lat = statistics.median(g[0] for g in geocoded)
            centroid_lng = statistics.median(g[1] for g in geocoded)
            fallback_count = 0
            for app in apps:
                if not app.get("lat"):
                    app["lat"] = centroid_lat
                    app["lng"] = centroid_lng
                    app["geocode_quality"] = "centroid"
                    fallback_count += 1
            if fallback_count:
                print(f"    [{portal.council_name}] Council centroid fallback for {fallback_count} apps")

        records = [{
            "council_id":       cid,
            "reference":        a["reference"],
            "address":          a.get("address"),
            "postcode":         a.get("postcode"),
            "lat":              a.get("lat"),
            "lng":              a.get("lng"),
            "description":      a.get("description"),
            "application_type": a.get("application_type"),
            "status":           a.get("status", "pending"),
            "submitted_date":   a.get("submitted_date"),
            "decision_date":    a.get("decision_date"),
            "council_url":      a.get("council_url"),
            "source":           "ni_scraper",
        } for a in apps]

        print(f"    [{portal.council_name}] Upserting {len(records)} records with council_id={cid}")

        BATCH = 20
        saved = 0
        ok = True
        for i in range(0, len(records), BATCH):
            if await _supa_upsert(records[i:i + BATCH]):
                saved += len(records[i:i + BATCH])
            else:
                ok = False

        if ok:
            await _supa_patch_council(cid, {
                "coverage_source": "ni_scraper",
                "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_empty_runs": 0,
                "active": True,
            })
            print(f"    [{portal.council_name}] ✓ Saved {saved}")
        else:
            print(f"    [{portal.council_name}] ⚠ Partial save: {saved} of {len(apps)}")
            if saved > 0:
                await _supa_patch_council(cid, {
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                    "consecutive_empty_runs": 0,
                })
        return saved


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind NI Planning Portal scraper (direct API)")
    print(f"Mode:        FAST ({DAYS_BACK} days back)")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL else 'NOT SET'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    try:
        from ni_councils import NI_COUNCILS, COUNCIL_DB_IDS
    except ImportError:
        print("ERROR: ni_councils.py not found")
        sys.exit(1)

    # REFUSE to silently skip a council whose DB id is still a
    # placeholder None — every other platform's onboarding process in
    # this project explicitly hands the user real INSERT SQL before the
    # scraper's first run (see §9 of the handoff process); a None id
    # here means that step hasn't happened yet, and running anyway
    # would either crash on the upsert or, worse, silently do nothing
    # for that council forever without ever saying why.
    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder "
              "(None) DB id in ni_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun ni_councils.py's INSERT_SQL in Supabase first, then "
              "replace each None above with the real id Supabase assigns "
              "(SELECT id, name FROM councils WHERE name = '...').")
        sys.exit(1)

    to_scrape = [
        NIPortal(name, authority_id, COUNCIL_DB_IDS[name])
        for name, authority_id in NI_COUNCILS
    ]

    print(f"Scraping {len(to_scrape)} councils via direct API (no browser needed)…\n")

    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(
            *[process_council(p, client, sem, DAYS_BACK) for p in to_scrape],
            return_exceptions=True,
        )

    total = sum(r for r in results if isinstance(r, int))
    time_skipped = sum(1 for r in results if r == "TIME_BUDGET_SKIP")
    zero_result = sum(1 for r in results if r == 0)
    errors = sum(1 for r in results if isinstance(r, Exception))

    print("\n" + "=" * 50)
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")
    if zero_result:
        print(f"Saved 0 (other reason — see per-council log lines above): {zero_result} councils")
    if time_skipped:
        print(f"Skipped (time budget): {time_skipped} councils")
    if errors:
        print(f"Errors: {errors}")
        for r in results:
            if isinstance(r, Exception):
                print(f"  {r!r}")


if __name__ == "__main__":
    asyncio.run(main())
