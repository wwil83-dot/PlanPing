#!/usr/bin/env python3
"""
PlanFind - Elmbridge Borough Council scraper (2026-11-16). Elmbridge runs its own system (emaps.elmbridge.gov.uk/ebc_planning.aspx),
not Idox. Probes 1 and 2 showed:
  * the advanced search is a plain GET (no browser, no cookies): daterec_from/daterec_to (received) or datedec_from/datedec_to
    (decided), pagerecs up to 500, pageno for more. The list gives only number / address / proposal / link: no status, no dates.
  * the detail page gives 'Received On', 'Valid From', 'Decision Date', 'Decision', 'Status', 'Application Type'.
So: RECEIVED sweep in one-day windows (the window IS the submitted date) = new applications, pending; DECIDED sweep in short windows,
then one detail page per decided application for the decision, its date and the received date.
Env: SUPABASE_URL, SUPABASE_KEY, ELMBRIDGE_COUNCIL_ID, DAYS_BACK (14), DAYS_END_AGO (0; a window is [today-DAYS_BACK, today-DAYS_END_AGO],
so a backfill can run in chunks), DECIDED_SLICE_DAYS (7), MAX_MINUTES (25), DRY_RUN (1 = write nothing), REQUEST_DELAY_SECONDS (1.2).
"""
import asyncio, os, re, statistics, sys, time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, quote

import httpx
from bs4 import BeautifulSoup

BASE = "https://emaps.elmbridge.gov.uk/ebc_planning.aspx"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
COUNCIL_ID = int(os.environ.get("ELMBRIDGE_COUNCIL_ID", "0"))
DAYS_BACK = int(os.environ.get("DAYS_BACK", "14"))
DAYS_END_AGO = int(os.environ.get("DAYS_END_AGO", "0"))
SLICE = int(os.environ.get("DECIDED_SLICE_DAYS", "7"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "25"))
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
DELAY = float(os.environ.get("REQUEST_DELAY_SECONDS", "1.2"))
RECS = 500
START = time.monotonic()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "Accept-Language": "en-GB,en;q=0.9"}
PROBLEMS: list = []
UNMAPPED: dict = {}


def minutes() -> float:
    return (time.monotonic() - START) / 60


def results_url(kind: str, a: str, b: str, page: int = 1) -> str:
    q = {"pageno": page, "template": "AdvancedSearchResultsTab.tmplt", "requestType": "parseTemplate", "USRN:PARAM": "",
         "apptype:PARAM": "", "status:PARAM": "", "decision:PARAM": "", "ward:PARAM": "", "txt_search:PARAM": "",
         "daterec_from:PARAM": a if kind == "rec" else "", "daterec_to:PARAM": b if kind == "rec" else "",
         "datedec_from:PARAM": a if kind == "dec" else "", "datedec_to:PARAM": b if kind == "dec" else "",
         "pagerecs": RECS, "orderxyz:PARAM": "REG_DATE_DT:DESCENDING", "SearchType:PARAM": "Advanced"}
    return BASE + "?" + urlencode(q)


def detail_url(ref: str, address: str) -> str:
    return (f"{BASE}?requesttype=parsetemplate&template=PlanningDetailsTab.tmplt&basepage=ebc_planning.aspx"
            f"&Filter=^APPLICATION_NUMBER^='{ref}'&appno:PARAM={ref}&address:PARAM={address}")


def parse_rows(html: str) -> list:
    out = []
    for tr in BeautifulSoup(html, "html.parser").select("table tr"):
        td = tr.find_all("td")
        if len(td) < 3:
            continue
        ref = td[0].get_text(strip=True)
        if not re.match(r"^\d{4}/\d+", ref):
            continue
        out.append({"reference": ref, "address": " ".join(td[1].get_text(" ", strip=True).split()),
                    "description": " ".join(td[2].get_text(" ", strip=True).split())})
    return out


def d_iso(s: str):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    f = {}
    for tr in soup.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th and td:
            f[" ".join(th.get_text(" ", strip=True).lower().rstrip(": ").split())] = " ".join(td.get_text(" ", strip=True).split())
    for dt in soup.find_all("dt"):                      # Elmbridge's detail page is a <dl>: <dt>Decision :</dt><dd>...</dd>
        dd = dt.find_next_sibling("dd")
        k = " ".join(dt.get_text(" ", strip=True).lower().rstrip(": ").split())
        if dd and k:
            f.setdefault(k, " ".join(dd.get_text(" ", strip=True).split()))
    txt = " ".join(soup.get_text(" ").split())
    for key, pat in (("received", r"Received On\s*:\s*(\d{1,2}/\d{1,2}/\d{4})"), ("valid", r"Valid From\s*:\s*(\d{1,2}/\d{1,2}/\d{4})"),
                     ("decision_date", r"Decision Date\s*:\s*(\d{1,2}/\d{1,2}/\d{4})")):
        m = re.search(pat, txt)
        f[key] = d_iso(m.group(1)) if m else None
    return f


def map_status(decision: str, status: str) -> str:
    d, s = (decision or "").strip().lower(), (status or "").strip().lower()
    if "withdraw" in s or "withdraw" in d:
        return "withdrawn"
    if not d or d == "pending":
        return "pending"
    if d == "unknown":
        return "closed"
    if re.search(r"raise objection|returned|finally disposed|advice letter|no further action|decline to determine|deferred|quashed|for information|"
                 r"insufficient|unsatisfactory|assessment|scoping|impact assessment|environmental|formal action|referral|negotiation|unilateral|"
                 r"in part-refuse|confirm in part", d):
        return "closed"
    if re.search(r"grant in part|in part/allow|in part/grant", d):
        return "approved"
    if "refus" in d:
        return "refused"
    if re.search(r"grant|approv|accept|no objection|not required|confirm compliance|permit|consent|lawful|compliance", d):
        return "approved"
    if d not in UNMAPPED:
        UNMAPPED[d] = 1
        print(f"    ⚠ UNMAPPED DECISION {decision!r} (status {status!r}): filed as 'closed'")
    return "closed"


def postcode(addr: str):
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", (addr or "").upper())
    return m.group(1) if m else None


async def get(c: httpx.AsyncClient, url: str, tries: int = 4):
    last = None
    for n in range(tries):
        try:
            await asyncio.sleep(DELAY)
            r = await c.get(url)
            if r.status_code in (429, 500, 502, 503, 504) and n < tries - 1:
                await asyncio.sleep(10 * (n + 1))
                continue
            return r
        except Exception as e:
            last = e
            await asyncio.sleep(3 + 4 * n)
    raise RuntimeError(f"GET failed: {type(last).__name__}: {str(last)[:100]}")


async def search(c, kind: str, a: str, b: str) -> list:
    out, page = [], 1
    while True:
        r = await get(c, results_url(kind, a, b, page))
        if r.status_code != 200:
            raise RuntimeError(f"results HTTP {r.status_code}")
        rows = parse_rows(r.text)
        out.extend(rows)
        if len(rows) < RECS or page >= 20:
            return out
        page += 1


async def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Elmbridge scraper   DRY_RUN {DRY_RUN}   days back {DAYS_BACK} (end {DAYS_END_AGO} ago)   "
          f"SUPABASE {'set' if SUPABASE_URL and SUPABASE_KEY else 'NOT set'}")
    if not DRY_RUN and not (SUPABASE_URL and SUPABASE_KEY and COUNCIL_ID):
        print("ERROR: SUPABASE_URL / SUPABASE_KEY / ELMBRIDGE_COUNCIL_ID not set")
        return 1
    today = datetime.now(timezone.utc).date()
    start, end = today - timedelta(days=DAYS_BACK), today - timedelta(days=DAYS_END_AGO)
    apps: dict = {}
    async with httpx.AsyncClient(headers=H, timeout=60, follow_redirects=True, verify=False) as c:
        # 1) RECEIVED, one day at a time: the window is the submitted date
        d, nrec = start, 0
        while d <= end:
            if minutes() > MAX_MINUTES:
                PROBLEMS.append(f"MAX_MINUTES reached during the received sweep at {d}")
                break
            try:
                for row in await search(c, "rec", d.isoformat(), d.isoformat()):
                    if row["reference"] not in apps:
                        apps[row["reference"]] = {**row, "submitted_date": d.isoformat(), "status": "pending", "decision_date": None,
                                                  "application_type": None}
                        nrec += 1
            except Exception as e:
                PROBLEMS.append(f"received {d}: {type(e).__name__}: {str(e)[:100]}")
            d += timedelta(days=1)
        print(f"    received sweep {start}..{end}: {nrec} applications")
        # 2) DECIDED, short windows, then one detail page each
        decided = {}
        a = start
        while a <= end:
            b = min(a + timedelta(days=SLICE - 1), end)
            if minutes() > MAX_MINUTES:
                PROBLEMS.append(f"MAX_MINUTES reached during the decided sweep at {a}")
                break
            try:
                for row in await search(c, "dec", a.isoformat(), b.isoformat()):
                    decided.setdefault(row["reference"], row)
            except Exception as e:
                PROBLEMS.append(f"decided {a}..{b}: {type(e).__name__}: {str(e)[:100]}")
            a = b + timedelta(days=1)
        print(f"    decided sweep: {len(decided)} applications; reading their detail pages")
        n_ok = n_bad = 0
        shown = 0
        for ref, row in decided.items():
            if minutes() > MAX_MINUTES:
                PROBLEMS.append(f"MAX_MINUTES reached while reading decision pages ({n_ok} of {len(decided)} read)")
                break
            try:
                r = await get(c, detail_url(ref, row["address"]))
                f = parse_detail(r.text) if r.status_code == 200 else {}
            except Exception as e:
                f = {}
            if not f.get("decision") and not f.get("decision_date"):
                n_bad += 1
                if n_bad <= 3:
                    print(f"    ⚠ no decision read for {ref} (detail page HTTP {getattr(r, 'status_code', '?')}; fields {list(f)[:6]})")
                continue
            n_ok += 1
            rec = apps.setdefault(ref, {**row, "submitted_date": None, "application_type": None})
            rec["status"] = map_status(f.get("decision"), f.get("status"))
            rec["decision_date"] = f.get("decision_date") or rec.get("decision_date")
            rec["submitted_date"] = f.get("received") or f.get("valid") or rec.get("submitted_date")
            rec["application_type"] = f.get("application type") or rec.get("application_type")
            if f.get("description"):
                rec["description"] = f["description"]
            if shown < 3:
                shown += 1
                print(f"    sample decision: {ref} | {f.get('decision')!r} / {f.get('status')!r} -> {rec['status']} | decided {rec['decision_date']} | received {rec['submitted_date']}")
        print(f"    detail pages: {n_ok} read, {n_bad} without a decision")
        if DRY_RUN:                                      # check the stable detail-page link we will store really opens that application
            some = next(iter(apps.values()), None)
            if some:
                r = await get(c, detail_url(some["reference"], some["address"]))
                print(f"    stored-link check {some['reference']}: HTTP {r.status_code}, reference on page: {some['reference'] in r.text}")
    recs_all = list(apps.values())
    from collections import Counter
    print(f"\n{len(recs_all)} applications; status {dict(Counter(a['status'] for a in recs_all))}; "
          f"with decision date {sum(1 for a in recs_all if a.get('decision_date'))}; without submitted date {sum(1 for a in recs_all if not a.get('submitted_date'))}")
    for a in recs_all[:5]:
        print("   ", a["reference"], "|", a["address"][:50], "|", a["status"], "|", a.get("submitted_date"), "|", (a.get("description") or "")[:50])
    saved = 0
    if not DRY_RUN and recs_all:
        saved = await save(recs_all)
    elif not DRY_RUN:
        await patch({"last_scraped_at": datetime.now(timezone.utc).isoformat()})
    print(f"\nFinished in {minutes():.1f} min   saved {saved}")
    if PROBLEMS:
        print("⚠ PROBLEMS:")
        for p in PROBLEMS:
            print("   ", p)
        return 1
    return 0


def _sh():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}


async def patch(data: dict):
    async with httpx.AsyncClient(timeout=15) as c:
        await c.patch(f"{SUPABASE_URL}/rest/v1/councils", params={"id": f"eq.{COUNCIL_ID}"}, json=data, headers={**_sh(), "Prefer": "return=minimal"})


async def geocode(pcs: list) -> dict:
    out = {}
    uniq = list({p.strip().upper().replace(" ", "") for p in pcs if p})
    async with httpx.AsyncClient(timeout=20) as c:
        for i in range(0, len(uniq), 100):
            try:
                r = await c.post("https://api.postcodes.io/postcodes", json={"postcodes": uniq[i:i + 100]})
                for item in r.json().get("result") or []:
                    res = item.get("result")
                    if res and res.get("latitude") is not None:
                        out[item["query"].upper().replace(" ", "")] = (res["latitude"], res["longitude"])
            except Exception as e:
                print(f"    geocode batch failed: {type(e).__name__}")
    return out


async def save(apps: list) -> int:
    pcs = [postcode(a["address"]) for a in apps]
    coords = await geocode([p for p in pcs if p])
    for a, p in zip(apps, pcs):
        a["postcode"] = p
        k = p.upper().replace(" ", "") if p else None
        if k in coords:
            a["lat"], a["lng"] = coords[k]
    geo = [(a["lat"], a["lng"]) for a in apps if a.get("lat")]
    if geo:
        clat, clng = statistics.median(g[0] for g in geo), statistics.median(g[1] for g in geo)
        for a in apps:
            if not a.get("lat"):
                a["lat"], a["lng"], a["geocode_quality"] = clat, clng, "centroid"
    records = [{"council_id": COUNCIL_ID, "reference": a["reference"], "address": a["address"] or None, "postcode": a.get("postcode"),
                "lat": a.get("lat"), "lng": a.get("lng"), "description": a.get("description") or None,
                "application_type": a.get("application_type") or "Planning", "status": a["status"],
                "submitted_date": a.get("submitted_date"), "decision_date": a.get("decision_date"),
                "council_url": detail_url(a["reference"], a["address"]), "source": "elmbridge_scraper"} for a in apps]
    saved, ok = 0, True
    async with httpx.AsyncClient(timeout=60) as c:
        for i in range(0, len(records), 50):
            chunk = records[i:i + 50]
            r = await c.post(f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference", json=chunk,
                             headers={**_sh(), "Prefer": "resolution=merge-duplicates,return=minimal"})
            if r.status_code in (200, 201, 204):
                saved += len(chunk)
            else:
                ok = False
                print(f"    ⚠ upsert failed HTTP {r.status_code}: {r.text[:200]}")
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        await patch({"coverage_source": "elmbridge_scraper", "last_scraped_at": now, "last_saved_at": now, "consecutive_empty_runs": 0, "active": True})
        print(f"    ✓ saved {saved}")
    return saved


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
