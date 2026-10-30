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
    ("Dumfries and Galloway Council", "https://eaccess.dumgal.gov.uk/online-applications", 355),  # Cloudflare for Playwright; passes with the Chrome fingerprint (probe 2026-10-27)
    # Reachable only through a UK residential proxy (probe 2026-10-28). Not in the nightly until each has had a dry run + backfill.
    ("Aberdeen City Council", "https://publicaccess.aberdeencity.gov.uk/online-applications", 1),
    ("Bury Metropolitan Borough Council", "https://planning.bury.gov.uk/online-applications", 170),
    ("City of Westminster", "https://idoxpa.westminster.gov.uk/online-applications", 267),
    ("Hart District Council", "https://publicaccess.hart.gov.uk/online-applications", 297),
    ("London Borough of Enfield", "https://planningandbuildingcontrol.enfield.gov.uk/online-applications", 242),
    ("Scottish Borders Council", "https://eplanning.scotborders.gov.uk/online-applications", 351),
    ("South Downs National Park Authority", "https://planningpublicaccess.southdowns.gov.uk/online-applications", 257),
    ("Watford Borough Council", "https://pa.watford.gov.uk/publicaccess", 449),
    ("Wolverhampton City Council", "https://planningonline.wolverhampton.gov.uk/online-applications", 79),

    # Reached through the UK proxies in the 2026-10-29 probe (round 2). Dry run first; not in any nightly yet.
    ("Gosport Borough Council", "https://publicaccess.gosport.gov.uk/online-applications", 296),
    ("Melton Borough Council", "https://pa.melton.gov.uk/online-applications", 446),
    ("Bassetlaw District Council", "https://publicaccess.bassetlaw.gov.uk/online-applications", 392),
    ("Calderdale Metropolitan Borough Council", "https://portal.calderdale.gov.uk/online-applications", 176),
    ("Cardiff Council", "https://www.cardiffidoxcloud.wales/publicaccess", 26),
    ("Clackmannanshire Council", "https://publicaccess.clacks.gov.uk/publicaccess", 342),
    ("Derby City Council", "https://eplanning.derby.gov.uk/online-applications", 32),
    ("Gloucester City Council", "https://publicaccess.gloucester.gov.uk/online-applications", 37),
    ("Harborough District Council", "https://pa2.harborough.gov.uk/online-applications", 360),
    ("Harlow District Council", "https://planningonline.harlow.gov.uk/online-applications", 285),
    ("Lancaster City Council", "https://planning.lancaster.gov.uk/online-applications", 308),
    ("London Borough of Bexley", "https://pa.bexley.gov.uk/online-applications", 228),
    ("London Borough of Kingston upon Thames", "https://publicaccess.kingston.gov.uk/online-applications", 234),
    ("London Borough of Newham", "https://pa.newham.gov.uk/online-applications", 223),
    ("London Borough of Southwark", "https://planning.southwark.gov.uk/online-applications", 219),
    ("London Borough of Sutton", "https://planningregister.sutton.gov.uk/online-applications", 232),
    ("North East Lincolnshire Council", "https://planninganddevelopment.nelincs.gov.uk/online-applications", 388),
    ("North Kesteven District Council", "https://planningonline.n-kesteven.gov.uk/online-applications", 478),
    ("South Kesteven District Council", "https://prod.publicaccess.southkesteven.gov.uk/online-applications", 479),
    ("East Riding of Yorkshire Council", "https://newplanningaccess.eastriding.gov.uk/newplanningaccess", 475),
    ("North Lanarkshire Council", "https://eplanning.northlanarkshire.gov.uk/online-applications", 350),
    ("North Northamptonshire Council", "https://publicaccess.northnorthants.gov.uk/online-applications", 387),
    ("North Yorkshire Council", "https://publicaccess.northyorks.gov.uk/online-applications", 371),
    ("Oxford City Council", "https://public.oxford.gov.uk/online-applications", 58),
    ("Sandwell Metropolitan Borough Council", "https://webcaps.sandwell.gov.uk/publicaccess", 190),
    ("Sevenoaks District Council", "https://pa.sevenoaks.gov.uk/online-applications", 271),
    ("Sheffield City Council", "https://planningapps.sheffield.gov.uk/online-applications", 63),
    ("Shropshire Council", "https://pa.shropshire.gov.uk/online-applications", 364),
    ("Somerset Council (Mendip)", "https://publicaccess.mendip.gov.uk/online-applications", 370),
    ("South Ayrshire Council", "https://publicaccess.south-ayrshire.gov.uk/online-applications", 339),
    ("South Gloucestershire Council", "https://developments.southglos.gov.uk/online-applications", 262),
    ("Southampton City Council", "https://planningpublicaccess.southampton.gov.uk/online-applications", 65),
    ("St. Helens Metropolitan Borough Council", "https://publicaccess.sthelens.gov.uk/online-applications", 181),
    ("Stafford Borough Council", "https://www12.staffordbc.gov.uk/online-applications", 476),
    ("Sunderland City Council", "https://online-applications.sunderland.gov.uk/online-applications", 68),
    ("Swansea Council", "https://property.swansea.gov.uk/online-applications", 373),
    ("Tendring District Council", "https://idox.tendringdc.gov.uk/online-applications", 265),
    ("Three Rivers District Council", "https://www3.threerivers.gov.uk/online-applications", 291),
    ("Uttlesford District Council", "https://publicaccess.uttlesford.gov.uk/online-applications", 288),
    ("West Berkshire Council", "https://publicaccess.westberks.gov.uk/online-applications", 258),
]
DAYS_BACK = int(os.environ.get("DAYS_BACK", "14"))
SLICE_DAYS = int(os.environ.get("SLICE_DAYS", "7"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "25"))
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
DELAY = float(os.environ.get("REQUEST_DELAY_SECONDS", "1.5"))
# HTTP_PROXY_URLS: optional comma list of http://user:pass@host:port. Each council sticks to one proxy (its cid picks it) so the
# session cookie and CSRF token stay on one IP; if a portal cannot be reached, the next proxy is tried. DIRECT councils never use one.
PROXIES = [x.strip() for x in os.environ.get("HTTP_PROXY_URLS", "").split(",") if x.strip()]
DIRECT = {389, 390, 391, 362, 361, 355}
# Portals that answer HTTP 429 at the normal pace (seen 2026-10-28): Westminster, Enfield, South Downs. Seconds between requests.
SLOW = {267: 6.0, 242: 5.0, 257: 5.0, 364: 10.0, 475: 3.0, 37: 3.5, 223: 3.5, 32: 3.5, 271: 3.5, 228: 3.5}
# Portals whose detail pages never say what was decided (only 'Decided'): reading them is wasted requests, so decided stay 'closed'.
NO_DETAIL = {351, 79, 181}
COOLDOWN = int(os.environ.get("DETAIL_COOLDOWN_SECONDS", "300"))      # first wait after a block; grows by 2 min each time a wait was not enough
# Certificate chains these portals do not send in full (probe 2026-10-29: fine with verification off; public pages, no logins).
INSECURE = {392, 176, 219, 58, 63, 181}
# Proxy that got through to each council in the probes (index into HTTP_PROXY_URLS). Several councils block some of our proxy
# addresses (timeouts on 0, 1, 2), so each council starts on a proxy known to work and falls back to the others.
PROXY_HINT = {1: 3, 26: 7, 32: 3, 37: 7, 58: 3, 63: 3, 65: 5, 68: 3, 79: 3, 170: 0, 176: 7, 181: 3, 190: 3, 219: 3, 223: 3, 228: 3, 232: 3, 234: 5, 242: 3, 257: 7, 258: 3, 262: 2, 265: 5, 267: 7, 271: 3, 285: 5, 288: 3, 291: 3, 296: 7, 446: 3, 297: 7, 308: 3, 339: 3, 342: 3, 350: 3, 351: 3, 360: 3, 364: 5, 370: 3, 371: 3, 373: 3, 387: 7, 388: 3, 392: 3, 449: 3, 476: 6, 478: 3}
ONLY = {int(x) for x in os.environ.get("HTTP_COUNCILS", "").replace(" ", "").split(",") if x}
MAX_PAGES = 80
START = time.monotonic()
CALLS = {"n": 0}


def minutes() -> float:
    return (time.monotonic() - START) / 60


def card_fields(html: str) -> dict:
    """reference -> raw metaInfo fields of every result card (same splitting as idox_scraper._parse_result)."""
    from bs4 import BeautifulSoup
    out = {}
    for li in BeautifulSoup(html, "html.parser").select("li.searchresult"):
        f = {}
        meta = li.find(class_=re.compile(r"metaInfo|meta-info|metadata", re.I))
        if meta:
            for part in meta.get_text(separator="|", strip=True).split("|"):
                if ":" in part:
                    k, _, v = part.partition(":")
                    f[k.strip().lower()] = v.strip()
        ref = (f.get("ref. no") or f.get("ref no") or f.get("reference") or "").strip()
        if ref:
            out[ref] = f
    return out


RAW_STATUS = {}                      # council -> Counter of raw status/decision labels seen


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
        self.pi = PROXY_HINT.get(cid, cid) % len(PROXIES) if PROXIES else 0

    def mk_session(self):
        if PROXIES and self.cid not in DIRECT:
            px = PROXIES[self.pi % len(PROXIES)]
            return AsyncSession(impersonate="chrome", proxies={"http": px, "https": px}, verify=self.cid not in INSECURE)
        return AsyncSession(impersonate="chrome", verify=self.cid not in INSECURE)

    @property
    def delay(self) -> float:
        return SLOW.get(self.cid, DELAY)

    def log(self, msg: str):
        print(f"    [{self.name}] {msg}")

    async def get(self, path: str, tries: int = 4, timeout: int = 60, fast: bool = False, **kw):
        last = None
        for n in range(tries):
            try:
                await asyncio.sleep(self.delay)
                CALLS["n"] += 1
                r = await self.session.get(self.base + path, timeout=timeout, **kw)
                if r.status_code == 429 and n < tries - 1:
                    await asyncio.sleep(20 * (n + 1))      # rate limited: wait it out (20s, 40s, 60s)
                    continue
                if r.status_code >= 500 and n < tries - 1:
                    await asyncio.sleep(3 + 4 * n)
                    continue
                return r
            except Exception as e:
                last = e
                if fast:
                    break
                await asyncio.sleep(2 + 3 * n)
        raise RuntimeError(f"GET {path[:60]} failed: {type(last).__name__}: {str(last)[:100]}")

    async def post(self, path: str, data: dict, tries: int = 4):
        last = None
        for n in range(tries):
            try:
                await asyncio.sleep(self.delay)
                CALLS["n"] += 1
                r = await self.session.post(self.base + path, data=data, timeout=90,
                                            headers={"Referer": f"{self.base}/search.do?action=advanced&searchType=Application"})
                if r.status_code == 429 and n < tries - 1:
                    await asyncio.sleep(20 * (n + 1))
                    continue
                if r.status_code >= 500 and n < tries - 1:
                    await asyncio.sleep(3 + 4 * n)
                    continue
                return r
            except Exception as e:
                last = e
                await asyncio.sleep(2 + 3 * n)
        raise RuntimeError(f"POST {path[:60]} failed: {type(last).__name__}: {str(last)[:100]}")

    async def search(self, kind: str, a: date, b: date) -> list:
        """kind: 'received' | 'decided'. Returns parsed applications for the window, or raises TooMany / Blocked."""
        self.session = self.mk_session()
        try:
            r = None
            use_proxy = bool(PROXIES) and self.cid not in DIRECT
            for attempt in range(5 if use_proxy else 1):
                try:
                    r = await self.get("/search.do?action=advanced&searchType=Application", tries=3,
                                       timeout=25 if use_proxy else 60, fast=use_proxy)
                    break
                except RuntimeError as e:
                    self.log(f"search form unreachable (proxy {self.pi % max(1, len(PROXIES))}): {str(e)[:90]}")
                    if attempt >= 4 or not use_proxy:
                        raise
                    self.pi += 1
                    self.session = self.mk_session()
            if r.status_code == 429 and use_proxy:
                for _ in range(3):                     # rate limited on this address: try the form from the next proxy
                    self.pi += 1
                    self.log(f"search form HTTP 429: trying proxy {self.pi % len(PROXIES)}")
                    await self.session.close()
                    self.session = self.mk_session()
                    await asyncio.sleep(15)
                    r = await self.get("/search.do?action=advanced&searchType=Application", tries=1, timeout=25, fast=True)
                    if r.status_code != 429:
                        break
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
                fields = card_fields(r.text)
                if not fields:
                    break                          # the form page again / no results: nothing to parse (and no noisy diagnostic)
                got, has_next = I.parse_results_page(r.text, self.base, self.root, self.name)
                for g in got:
                    g["_fields"] = fields.get(g.get("reference"), {})
                if not got:
                    self.log("⚠ page %d: cards present but none parsed; starts %r" % (page, " ".join(r.text[:200].split())))
                apps.extend(got)
                if not has_next or not got or page >= MAX_PAGES:
                    break
                page += 1
                r = await self.get(f"/pagedSearchResults.do?action=page&searchCriteria.page={page}", tries=6)
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
        f = app.pop("_fields", {}) or {}
        raw = (f.get("decision") or f.get("status") or "").strip()
        RAW_STATUS.setdefault(p.name, {}).setdefault(raw or "(none)", 0)
        RAW_STATUS[p.name][raw or "(none)"] += 1
        status = I._normalise_status(raw) if raw else app.get("status", "pending")
        if kind == "decided":
            if status == "pending":
                status = "closed"                  # it was found by DECISION date, so a decision exists; the label is not one we map
            if not app.get("decision_date"):
                app["decision_date"] = b.isoformat()   # best guess (window end); the detail page gives the real date
                app["_dd_guess"] = True
        app["status"] = status
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
        a = b + timedelta(days=1)
    a = start
    while a <= today:
        b = min(a + timedelta(days=SLICE_DAYS - 1), today)
        await sweep(p, "decided", a, b, out, problems)
        a = b + timedelta(days=1)
    return p, list(out.values()), problems


def outcome(raw: str) -> str:
    """Decision label -> approved / refused / withdrawn / closed (decided, outcome neutral), or 'pending' when not recognised."""
    t = (raw or "").lower().strip()
    if not t:
        return "pending"
    if "withdraw" in t:
        return "withdrawn"
    if re.search(r"not discharged|further discharge|details required|approval required|permission required|determine not|"
                 r"recommendation only|request responded|unknown|not to make", t):
        return "closed"
    if re.search(r"refus|unlawful|not lawful|invalid|reject|dismiss", t):
        return "refused"
    if re.search(r"approv|grant|permit|permission|discharg|lawful|certificate|no objection|not required|no further|consent|"
                 r"section 106|split|agreed|conditional|outline|reserved|determined|acceptable|advertisement|non material|allow", t):
        return "approved"
    return "pending"


OUTCOMES_SEEN = {}                    # council -> {label: mapped status}
DETAIL_MAX = int(os.environ.get("DETAIL_MAX", "150"))
_DETAIL_SHOWN = set()


def detail_fields(html: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    f = {}
    for tr in soup.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th and td:
            f[" ".join(th.get_text(" ", strip=True).lower().rstrip(":").split())] = " ".join(td.get_text(" ", strip=True).split())
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            f.setdefault(" ".join(dt.get_text(" ", strip=True).lower().rstrip(":").split()), " ".join(dd.get_text(" ", strip=True).split()))
    return f


async def stored_outcomes(p: Portal) -> dict:
    """{reference: (status, decision_date)} for this council's applications that already have a final outcome in the database,
    so each night only newly decided applications cost a detail-page request. Empty on DRY_RUN or if the lookup fails."""
    if DRY_RUN or not (I.SUPABASE_URL and I.SUPABASE_KEY):
        return {}
    since = (datetime.now(timezone.utc).date() - timedelta(days=DAYS_BACK + 7)).isoformat()
    out, off = {}, 0
    try:
        while off < 20000:
            rows = await I._supa_get("planning_applications", select="reference,status,decision_date", council_id=f"eq.{p.cid}",
                                     status="in.(approved,refused,withdrawn)", decision_date=f"gte.{since}", limit=1000, offset=off)
            for r in rows:
                out[r["reference"]] = (r["status"], r.get("decision_date"))
            if len(rows) < 1000:
                break
            off += 1000
    except Exception as e:
        p.log(f"stored-outcome lookup failed ({type(e).__name__}: {str(e)[:80]}); reading all detail pages")
        return {}
    return out


async def add_outcomes(p: Portal, apps: list, problems: list):
    """The result cards of these portals say 'decided' but not what was decided. For every such application read the detail page
    (Decision / Decision Issued Date) so approved / refused / withdrawn are right."""
    todo = [a for a in apps if (a.get("status") == "closed" or a.get("_dd_guess")) and a.get("council_url")]
    if not todo or p.cid in NO_DETAIL:
        return
    known = await stored_outcomes(p)
    if known:
        left = []
        for a in todo:
            k = known.get(a.get("reference"))
            if k:
                a["status"] = k[0]
                if k[1] and not a.get("decision_date"):
                    a["decision_date"] = k[1]
                a.pop("_dd_guess", None)
            else:
                left.append(a)
        p.log(f"{len(todo) - len(left)} of {len(todo)} decided applications already have an outcome in the database: detail page not re-read")
        todo = left
        if not todo:
            return
    todo = todo[:DETAIL_MAX]
    p.log(f"reading {len(todo)} decision detail pages")
    p.session = p.mk_session()
    fixed = 0
    http = {}
    nodec = 0
    strikes = 0
    waits = 0
    ok_run = 0
    cool = COOLDOWN
    try:
        for a in todo:
            if minutes() > MAX_MINUTES:
                problems.append(f"{p.name}: MAX_MINUTES reached while reading decision details")
                break
            url = a["council_url"]
            r = None
            for n in range(3):
                try:
                    await asyncio.sleep(p.delay)
                    CALLS["n"] += 1
                    r = await p.session.get(url, timeout=60)
                except Exception as e:
                    http[type(e).__name__] = http.get(type(e).__name__, 0) + 1
                    r = None
                    break
                if r.status_code == 429 and n < 2:
                    await asyncio.sleep(20 * (n + 1))
                    continue
                break
            if r is None:
                continue
            if r.status_code in (429, 403):
                strikes += 1
                http[str(r.status_code)] = http.get(str(r.status_code), 0) + 1
                if strikes >= 2:
                    # These portals allow only about a dozen detail pages and then block our address for a few minutes (a 10 minute
                    # wait always worked; East Riding answers 403 instead of 429). Wait it out, then carry on.
                    if ok_run == 0 and waits:
                        cool += 120                    # the last wait was not long enough
                    waits += 1
                    if waits > 14:
                        problems.append(f"{p.name}: detail pages still blocked (HTTP {r.status_code}) after {waits - 1} waits; stopped, the next run continues")
                        break
                    p.log(f"detail pages blocked after {ok_run} pages in a row (HTTP {r.status_code}): waiting {cool // 60} min (wait {waits})")
                    await asyncio.sleep(cool)
                    strikes = 0
                    ok_run = 0
                continue
            if r.status_code != 200:
                http[str(r.status_code)] = http.get(str(r.status_code), 0) + 1
                continue
            if waits and ok_run == 0:
                p.log(f"detail pages open again after the wait ({waits} so far)")
            strikes = 0
            ok_run += 1
            http["200"] = http.get("200", 0) + 1
            f = detail_fields(r.text)
            if p.name not in _DETAIL_SHOWN:
                _DETAIL_SHOWN.add(p.name)
                p.log(f"detail page fields for {a['reference']}: { {k: v[:40] for k, v in list(f.items())[:18]} }")
            raw = (f.get("decision") or "").strip()
            st = outcome(raw)
            if st in ("pending", "closed") and f.get("status"):
                st2 = outcome(f["status"])                 # e.g. Hinckley: decision 'Planning Permission', status 'Permitted'
                if st2 not in ("pending", "closed"):
                    st, raw = st2, f["status"]
            if not raw:
                raw = (f.get("status") or "").strip()
            if st in ("pending", "closed") and nodec < 3:
                nodec += 1
                p.log(f"no outcome on detail page {a['reference']}: { {k: v[:30] for k, v in list(f.items())[:14]} }")
            OUTCOMES_SEEN.setdefault(p.name, {})
            OUTCOMES_SEEN[p.name][raw or "(none)"] = OUTCOMES_SEEN[p.name].get(raw or "(none)", [st, 0])
            if isinstance(OUTCOMES_SEEN[p.name][raw or "(none)"], list):
                OUTCOMES_SEEN[p.name][raw or "(none)"][1] += 1
            if st != "pending":
                a["status"] = st
                fixed += 1
            for key in ("decision issued date", "decision made date", "decision date", "decision notice sent date"):
                d = I._parse_date(f.get(key, ""))
                if d:
                    a["decision_date"] = d
                    a.pop("_dd_guess", None)
                    break
    finally:
        try:
            await p.session.close()
        except Exception:
            pass
    p.log(f"detail pages HTTP: {http}")
    p.log(f"outcome found for {fixed} of {len(todo)}")
    seen = OUTCOMES_SEEN.get(p.name, {})
    if seen:
        p.log("decision labels -> status: " + "; ".join(f"{k!r} -> {v[0]} x{v[1]}" for k, v in sorted(seen.items(), key=lambda kv: -kv[1][1])[:15]))


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
        a.pop("_dd_guess", None)
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


async def process(name: str, base: str, cid: int, quiet_samples: bool = False) -> tuple:
    p, apps, problems = await run_council(name, base, cid)
    await add_outcomes(p, apps, problems)
    st = {}
    for a in apps:
        st[a.get("status", "pending")] = st.get(a.get("status", "pending"), 0) + 1
    dec = sum(1 for a in apps if a.get("decision_date"))
    nosub = sum(1 for a in apps if not a.get("submitted_date"))
    p.log(f"raw status labels seen: {dict(sorted(RAW_STATUS.get(name, {}).items(), key=lambda kv: -kv[1])[:12])}")
    p.log(f"{len(apps)} applications; status {st}; with decision date {dec}; without submitted date {nosub}")
    if DRY_RUN:
        for a in apps[:3]:
            p.log(f"sample: {a.get('reference')} | {a.get('status')} | sub {a.get('submitted_date')} | dec {a.get('decision_date')} | "
                  f"{(a.get('address') or '')[:50]} | {(a.get('description') or '')[:50]}")
        return 0, problems
    if not apps:
        await I._supa_patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        if not problems:
            await I._supa_increment_empty_runs(cid)
        return 0, problems
    return await geocode_and_save(p, apps), problems


async def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Idox HTTP scraper (curl_cffi)")
    print(f"Days back {DAYS_BACK}   slice {SLICE_DAYS}d   DRY_RUN {DRY_RUN}   max {MAX_MINUTES} min   detail cap {DETAIL_MAX}   "
          f"SUPABASE {'set' if I.SUPABASE_URL and I.SUPABASE_KEY else 'NOT set'}\n")
    todo = [c for c in COUNCILS if not ONLY or c[2] in ONLY]
    print(f"Proxies: {len(PROXIES)} loaded (direct for ids {sorted(DIRECT)})")
    print(f"Running {len(todo)} councils in parallel (different servers; each one is paced at {DELAY}s per request)\n")
    results = await asyncio.gather(*(process(*c) for c in todo), return_exceptions=True)
    total, all_problems = 0, []
    for c, r in zip(todo, results):
        if isinstance(r, Exception):
            all_problems.append(f"{c[0]}: {type(r).__name__}: {str(r)[:150]}")
        else:
            total += r[0]
            all_problems += r[1]
    print(f"\nHTTP requests: {CALLS['n']}   applications saved: {total}   {minutes():.1f} min")
    if all_problems:
        print("⚠ PROBLEMS:")
        for x in all_problems:
            print(f"    {x}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
