#!/usr/bin/env python3
"""
PlanFind - Idox portals that the Playwright scraper can no longer drive, scraped over plain HTTP (curl_cffi, Chrome fingerprint).

Why: seven Idox councils went stale between July and September 2026.
  * North East Derbyshire, Bolsover, Derbyshire Dales sit behind a Cloudflare challenge that headless Chromium fails, but the
    Chrome-fingerprint HTTP client gets the real search form and a real advanced search (probe idox_stale7_probe2).
  * Blaby and Hinckley & Bosworth added a reCAPTCHA button to the monthly list, so the Playwright click never submits.
    The advanced search does not need it.
How: open the advanced search form (cookie + _csrf), POST advancedSearchResults.do with a date range, then page through
pagedSearchResults.do. Two sweeps per window: date RECEIVED (new applications) and date DECIDED (decisions on older ones).
Result cards are parsed with idox_scraper.parse_results_page, so every field is read exactly as in the nightly scraper, and rows
are saved in the same shape (source/coverage_source stay 'idox_scraper').

Env: SUPABASE_URL, SUPABASE_KEY (not needed for DRY_RUN), DAYS_BACK (14), SLICE_DAYS (7; a window the portal calls 'too many
results' is halved), MAX_MINUTES (25), DRY_RUN (1 = write nothing), HTTP_COUNCILS (optional comma list of council ids to run),
REQUEST_DELAY_SECONDS (1.5).
"""
import asyncio
import os
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

import idox_scraper as I

COUNCILS = [
    ("North East Derbyshire District Council", "https://planapps-online.ne-derbyshire.gov.uk/online-applications", 389),
    ("Bolsover District Council", "https://publicaccess.bolsover.gov.uk/online-applications", 390),
    ("Derbyshire Dales District Council", "https://planning.derbyshiredales.gov.uk/online-applications", 391),
    ("Blaby District Council", "https://pa.blaby.gov.uk/online-applications", 362),
    ("Hinckley and Bosworth Borough Council", "https://pa.hinckley-bosworth.gov.uk/online-applications", 361),
]
DAYS_BACK = int(os.environ.get("DAYS_BACK", "14"))
SLICE_DAYS = int(os.environ.get("SLICE_DAYS", "7"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "25"))
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
DELAY = float(os.environ.get("REQUEST_DELAY_SECONDS", "1.5"))
ONLY = {int(x) for x in os.environ.get("HTTP_COUNCILS", "").replace(" ", "").split(",") if x}
MAX_PAGES = 80
START = time.monotonic()
CALLS = {"n": 0}


def minutes() -> float:
    return (time.monotonic() - START) / 60


class TooMany(Exception):
    pass


class Blocked(Exception):
    pass


def fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


class Portal:
    def __init__(self, name: str, base: str, cid: int):
        self.name, self.base, self.cid = name, base.rstrip("/"), cid
        p = urlparse(self.base)
        self.root = f"{p.scheme}://{p.netloc}"
        self.session = None

    def log(self, msg: str):
        print(f"    [{self.name}] {msg}")

    async def get(self, path: str, tries: int = 3, **kw):
        last = None
        for n in range(tries):
            try:
                await asyncio.sleep(DELAY)
                CALLS["n"] += 1
                r = await self.session.get(self.base + path, timeout=60, **kw)
                return r
            except Exception as e:
                last = e
                await asyncio.sleep(2 + 3 * n)
        raise RuntimeError(f"GET {path[:60]} failed: {type(last).__name__}: {str(last)[:100]}")

    async def post(self, path: str, data: dict, tries: int = 3):
        last = None
        for n in range(tries):
            try:
                await asyncio.sleep(DELAY)
                CALLS["n"] += 1
                return await self.session.post(self.base + path, data=data, timeout=90,
                                               headers={"Referer": f"{self.base}/search.do?action=advanced&searchType=Application"})
            except Exception as e:
                last = e
                await asyncio.sleep(2 + 3 * n)
        raise RuntimeError(f"POST {path[:60]} failed: {type(last).__name__}: {str(last)[:100]}")

    async def search(self, kind: str, a: date, b: date) -> list:
        """kind: 'received' | 'decided'. Returns parsed applications for the window, or raises TooMany / Blocked."""
        self.session = AsyncSession(impersonate="chrome")
        try:
            r = await self.get("/search.do?action=advanced&searchType=Application")
            if r.status_code != 200 or "just a moment" in r.text[:3000].lower():
                raise Blocked(f"search form HTTP {r.status_code}")
            m = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text) or re.search(r'value="([^"]+)"\s+name="_csrf"', r.text)
            data = {"searchType": "Application", "searchCriteria.caseStatus": ""}
            if m:
                data["_csrf"] = m.group(1)
            if kind == "received":
                data["date(applicationReceivedStart)"], data["date(applicationReceivedEnd)"] = fmt(a), fmt(b)
            else:
                data["date(applicationDecisionStart)"], data["date(applicationDecisionEnd)"] = fmt(a), fmt(b)
            r = await self.post("/advancedSearchResults.do?action=firstPage", data)
            apps, page = [], 1
            while True:
                low = r.text.lower()
                if r.status_code != 200:
                    raise Blocked(f"results HTTP {r.status_code}")
                if "just a moment" in low[:4000]:
                    raise Blocked("Cloudflare challenge on results")
                if "too many results" in low or "enter some more parameters" in low:
                    raise TooMany()
                got, has_next = I.parse_results_page(r.text, self.base, self.root, self.name)
                apps.extend(got)
                if not has_next or not got or page >= MAX_PAGES:
                    break
                page += 1
                r = await self.get(f"/pagedSearchResults.do?action=page&searchCriteria.page={page}")
            return apps
        finally:
            try:
                await self.session.close()
            except Exception:
                pass


async def sweep(p: Portal, kind: str, a: date, b: date, out: dict, problems: list):
    if minutes() > MAX_MINUTES:
        problems.append(f"{p.name}: MAX_MINUTES reached before {kind} {a}..{b}")
        return
    try:
        apps = await p.search(kind, a, b)
    except TooMany:
        if a < b:
            mid = a + (b - a) // 2
            p.log(f"{kind} {a}..{b}: too many results, splitting")
            await sweep(p, kind, a, mid, out, problems)
            await sweep(p, kind, mid + timedelta(days=1), b, out, problems)
        else:
            problems.append(f"{p.name}: {kind} {a} has too many results for one day")
        return
    except Exception as e:
        problems.append(f"{p.name}: {kind} {a}..{b}: {type(e).__name__}: {str(e)[:120]}")
        p.log(f"⚠ {kind} {a}..{b}: {type(e).__name__}: {str(e)[:120]}")
        return
    new = 0
    for app in apps:
        ref = app.get("reference")
        if not ref:
            continue
        old = out.get(ref)
        if old is None:
            out[ref] = app
            new += 1
        elif (old.get("status", "pending") == "pending" and app.get("status", "pending") != "pending") or \
                (app.get("decision_date") and not old.get("decision_date")):
            out[ref] = {**old, **{k: v for k, v in app.items() if v not in (None, "")}}      # decision info wins
    p.log(f"{kind} {a}..{b}: {len(apps)} results ({new} new)")


async def run_council(name: str, base: str, cid: int) -> tuple:
    p = Portal(name, base, cid)
    out, problems = {}, []
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=DAYS_BACK)
    a = start
    while a <= today:
        b = min(a + timedelta(days=SLICE_DAYS - 1), today)
        await sweep(p, "received", a, b, out, problems)
        await sweep(p, "decided", a, b, out, problems)
        a = b + timedelta(days=1)
    return p, list(out.values()), problems


async def geocode_and_save(p: Portal, apps: list) -> int:
    need = [a["postcode"] for a in apps if not a.get("lat") and a.get("postcode")]
    if need:
        p.log(f"geocoding {len(set(need))} postcodes")
        coords = await I.geocode(need)
        for a in apps:
            if not a.get("lat") and a.get("postcode"):
                pc = a["postcode"].strip().upper().replace(" ", "")
                if pc in coords:
                    a["lat"], a["lng"] = coords[pc]
    geo = [(a["lat"], a["lng"]) for a in apps if a.get("lat") and a.get("lng")]
    if geo:
        clat, clng = statistics.median(g[0] for g in geo), statistics.median(g[1] for g in geo)
        n = 0
        for a in apps:
            if not a.get("lat"):
                a["lat"], a["lng"], a["geocode_quality"] = clat, clng, "centroid"
                n += 1
        if n:
            p.log(f"council centroid fallback for {n} apps")
    records, seen = [], set()
    for a in apps:
        if a["reference"] in seen:
            continue
        seen.add(a["reference"])
        records.append({
            "council_id": p.cid, "reference": a["reference"], "address": a.get("address"), "postcode": a.get("postcode"),
            "lat": a.get("lat"), "lng": a.get("lng"), "description": a.get("description"),
            "application_type": a.get("application_type"), "status": a.get("status", "pending"),
            "submitted_date": a.get("submitted_date"), "decision_date": a.get("decision_date"),
            "council_url": a.get("council_url"), "source": "idox_scraper",
        })
    saved, ok = 0, True
    for i in range(0, len(records), 20):
        if await I._supa_upsert(records[i:i + 20]):
            saved += len(records[i:i + 20])
        else:
            ok = False
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        await I._supa_patch_council(p.cid, {"coverage_source": "idox_scraper", "last_scraped_at": now, "last_saved_at": now,
                                            "consecutive_empty_runs": 0, "active": True})
        p.log(f"✓ saved {saved}")
    else:
        p.log(f"⚠ partial save {saved} of {len(records)}")
        if saved:
            await I._supa_patch_council(p.cid, {"last_saved_at": now, "consecutive_empty_runs": 0})
    return saved


async def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Idox HTTP scraper (curl_cffi)")
    print(f"Days back {DAYS_BACK}   slice {SLICE_DAYS}d   DRY_RUN {DRY_RUN}   max {MAX_MINUTES} min   "
          f"SUPABASE {'set' if I.SUPABASE_URL and I.SUPABASE_KEY else 'NOT set'}\n")
    all_problems, total = [], 0
    for name, base, cid in COUNCILS:
        if ONLY and cid not in ONLY:
            continue
        print(f"\n[{name}] (council_id={cid})")
        p, apps, problems = await run_council(name, base, cid)
        all_problems += problems
        st = {}
        for a in apps:
            st[a.get("status", "pending")] = st.get(a.get("status", "pending"), 0) + 1
        dec = sum(1 for a in apps if a.get("decision_date"))
        nosub = sum(1 for a in apps if not a.get("submitted_date"))
        p.log(f"{len(apps)} applications; status {st}; with decision date {dec}; without submitted date {nosub}")
        if DRY_RUN:
            for a in apps[:3]:
                print(f"        sample: {a.get('reference')} | {a.get('status')} | sub {a.get('submitted_date')} | dec {a.get('decision_date')} | "
                      f"{(a.get('application_type') or '')[:20]} | {(a.get('address') or '')[:50]} | {(a.get('description') or '')[:50]}")
            continue
        if not apps:
            await I._supa_patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
            if not problems:
                await I._supa_increment_empty_runs(cid)
            continue
        total += await geocode_and_save(p, apps)
    print(f"\nHTTP requests: {CALLS['n']}   applications saved: {total}   {minutes():.1f} min")
    if all_problems:
        print("⚠ PROBLEMS:")
        for x in all_problems:
            print(f"    {x}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
