"""
PlanFind - Wealden District Council scraper (planning.wealden.gov.uk, new register that replaced Idox).

How the site works (recon 2026-11-19):
  * Every first visit is redirected to a Copyright & Disclaimer page; POSTing its 'Agree' form sets a cookie.
  * /Search/Standard?DateValidFrom=MM/DD/YYYY 00:00:00&DateValidTo=...   (received/valid)
    /Search/Standard?DateDeterminedFrom=...&DateDeterminedTo=...         (decided)
    store the search in the session and land on /Search/Results (a 7-column table, 10 rows a page,
    paging links /Search/ResultsPage/N?module=PLA&tabOrder=0). Tab 0 is 'Planning (n)'.
  * Table columns: Application Number | Site Ref (UPRN) | Location | Description | Date Validated | Decision Date | Status Decision.
    'Status Decision' says 'Decision Issued' but NOT whether it was granted or refused, so decided applications get their detail page
    read (/Planning/Display/<ref>) for the outcome. Outcomes already in the database are reused, not re-read.
  * References ending /AA are Adjoining Authority consultations about land in ANOTHER council's area: skipped.

Env: WEALDEN_COUNCIL_ID (else looked up by name), WINDOW_DAYS (14), DETAIL_MAX (60), REQUEST_DELAY_SECONDS (1.2),
     MAX_MINUTES (30), DRY_RUN ("1" = write nothing; default 1), SUPABASE_URL, SUPABASE_KEY.
"""
import asyncio, os, re, sys, time, statistics
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

BASE = "https://planning.wealden.gov.uk"
SOURCE = "wealden_scraper"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "14"))
DETAIL_MAX = int(os.environ.get("DETAIL_MAX", "60"))
DELAY = float(os.environ.get("REQUEST_DELAY_SECONDS", "1.2"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "30"))
DRY_RUN = os.environ.get("DRY_RUN", "1").strip() in ("1", "true", "yes")
MAX_PAGES = 60
START = time.monotonic()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


# ---------------------------------------------------------------- helpers
def _dmy(s) -> Optional[date]:
    try:
        d = datetime.strptime((s or "").strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None
    return d if date(1990, 1, 1) < d <= date.today() + timedelta(days=366) else None


def _postcode(text: str) -> Optional[str]:
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", (text or "").upper())
    if not m:
        return None
    pc = m.group(1).replace(" ", "")
    return f"{pc[:-3]} {pc[-3:]}"


_KEEP_UPPER = {"TPO", "WD", "TM", "UPRN", "BNG", "EV", "HMO", "TV", "UK", "A", "B", "C", "II", "III", "IV", "NO"}


def _title_address(text: str) -> str:
    pc = _postcode(text)
    base = re.sub(r"\s+", " ", text or "").strip()
    if pc:
        base = re.sub(re.escape(pc).replace(r"\ ", r"\s?"), "", base, flags=re.I).strip(" ,")
    words = []
    for w in base.split(" "):
        if re.fullmatch(r"\d+[A-Z]?", w) or "/" in w:
            words.append(w.upper())
        else:
            words.append(w[:1].upper() + w[1:].lower() if w.isupper() or w.islower() else w)
    out = " ".join(words).replace(" ,", ",")
    return f"{out} {pc}".strip() if pc else out


def _sentence_case(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t or not t.isupper():
        return t
    t = t.lower()
    t = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    for ac in ("TPO", "WD", "UPRN", "BNG", "HMO", "EV", "TV"):
        t = re.sub(rf"\b{ac.lower()}\b", ac, t)
    return t


def _app_type(ref: str) -> str:
    suf = ref.rsplit("/", 1)[-1].upper()
    return {"F": "Full Planning Permission", "O": "Outline Planning Permission", "CD": "Discharge of Conditions",
            "TPO": "Tree Preservation Order Works", "LB": "Listed Building Consent"}.get(suf, suf)


_UNMAPPED: set = set()


def _normalise_outcome(s: str) -> Optional[str]:
    s = (s or "").strip().lower()
    if not s:
        return None
    if "withdraw" in s:
        return "withdrawn"
    if any(x in s for x in ("invalid", "returned", "closed", "cancel", "finally disposed", "not determined", "no further action")):
        return "closed"
    if "no objection" in s:
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not lawful", "unlawful", "not permit")):
        return "refused"
    if any(x in s for x in ("grant", "approv", "permit", "allow", "consent", "not required", "lawful", "discharge", "no objection", "agree")):
        return "approved"
    if s not in _UNMAPPED:
        _UNMAPPED.add(s)
        print(f"    ⚠ UNMAPPED OUTCOME {s!r}: saved as 'closed' until a mapping is added")
    return "closed"


def _status_from_table(text: str) -> str:
    t = (text or "").lower()
    if "withdraw" in t:
        return "withdrawn"
    if "decision" in t or "decided" in t or "determined" in t:
        return "decided"
    return "pending"


# ---------------------------------------------------------------- site access
async def accept_disclaimer(c: httpx.AsyncClient, r: httpx.Response) -> httpx.Response:
    for _ in range(3):
        if "disclaimer" not in str(r.url).lower():
            return r
        soup = BeautifulSoup(r.text, "html.parser")
        form = next((f for f in soup.find_all("form") if "Disclaimer/Accept" in (f.get("action") or "")), None)
        if not form:
            return r
        data = {i.get("name"): i.get("value", "") for i in form.find_all("input") if i.get("name")}
        r = await c.post(BASE + form.get("action"), data=data)
    return r


async def get(c: httpx.AsyncClient, url: str, params=None) -> httpx.Response:
    last = None
    for attempt in (1, 2, 3):
        try:
            r = await c.get(url, params=params)
            r = await accept_disclaimer(c, r)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:100]}"
        await asyncio.sleep(3 * attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def parse_results(html: str):
    soup = BeautifulSoup(html, "html.parser")
    tabs = [t.get_text(" ", strip=True) for t in soup.find_all(["button", "a", "li"]) if re.fullmatch(r"[A-Za-z ]+ \(\d+\)", t.get_text(" ", strip=True))]
    rows = []
    table = soup.find("table")
    if table:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue
            a = tr.find("a", href=True)
            c = [td.get_text(" ", strip=True) for td in tds]
            rows.append({"reference": c[0], "uprn": c[1], "location": c[2], "description": c[3],
                         "valid": c[4], "decided": c[5], "status_text": c[6], "href": a["href"] if a else None})
    nxt = None
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True).lower() == "next" and "ResultsPage" in a["href"]:
            nxt = a["href"]
            break
    return rows, tabs, nxt


async def sweep(c, kind: str, d_from: date, d_to: date) -> dict:
    """kind 'valid' or 'determined'. The end date is pushed a day later because the site's own weekly links end at 00:00."""
    pre = "DateValid" if kind == "valid" else "DateDetermined"
    params = {f"{pre}From": d_from.strftime("%m/%d/%Y") + " 00:00:00",
              f"{pre}To": (d_to + timedelta(days=1)).strftime("%m/%d/%Y") + " 00:00:00"}
    out, tabs_seen, total = {}, [], None
    r = await get(c, BASE + "/Search/Standard", params=params)
    for page in range(MAX_PAGES):
        rows, tabs, nxt = parse_results(r.text)
        if page == 0:
            tabs_seen = tabs
            m = next((re.search(r"\((\d+)\)", t) for t in tabs if t.lower().startswith("planning")), None)
            total = int(m.group(1)) if m else None
        for row in rows:
            out[row["reference"]] = row
        if not nxt or not rows:
            break
        await asyncio.sleep(DELAY)
        r = await get(c, BASE + nxt)
    note = f" (tab says {total})" if total is not None else ""
    flag = "" if total is None or total == len(out) else "   ⚠ COUNT MISMATCH"
    print(f"    ℹ {kind} {d_from}..{d_to}: {len(out)} rows{note}; tabs {tabs_seen}{flag}")
    return out


def parse_detail_fields(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    f = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            f[dt.get_text(" ", strip=True).rstrip(": ").lower()] = dd.get_text(" ", strip=True)
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            f.setdefault(cells[0].get_text(" ", strip=True).rstrip(": ").lower(), cells[1].get_text(" ", strip=True))
    for lab in soup.find_all(["label", "strong", "b", "span", "div"]):
        cls = " ".join(lab.get("class") or []).lower()
        if lab.name in ("label", "strong", "b") or "label" in cls or "title" in cls:
            k = lab.get_text(" ", strip=True).rstrip(": ").lower()
            if 0 < len(k) < 45 and k not in f:
                sib = lab.find_next_sibling()
                v = sib.get_text(" ", strip=True) if sib else ""
                if v and len(v) < 300:
                    f[k] = v
    return f


def outcome_from_fields(f: dict) -> Optional[str]:
    for k, v in f.items():
        if re.search(r"decision|outcome|determination", k) and not re.search(r"date|notice|level|type|by|expiry|due|target", k) and v:
            return v
    return None


# ---------------------------------------------------------------- supabase
def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}


async def council_id() -> int:
    env = os.environ.get("WEALDEN_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils", params={"select": "id,name,coverage_source", "name": "ilike.*Wealden*"}, headers=_h())
        r.raise_for_status()
    rows = r.json()
    if len(rows) != 1:
        raise RuntimeError(f"Expected one Wealden councils row, found {rows}. Set WEALDEN_COUNCIL_ID.")
    return rows[0]["id"]


async def existing_status(cid: int) -> dict:
    out = {}
    if not (SUPABASE_URL and SUPABASE_KEY):
        return out
    async with httpx.AsyncClient(timeout=30) as c:
        for page in range(12):
            r = await c.get(f"{SUPABASE_URL}/rest/v1/planning_applications",
                            params={"select": "reference,status", "council_id": f"eq.{cid}", "order": "id.desc"},
                            headers={**_h(), "Range": f"{page * 1000}-{page * 1000 + 999}"})
            if r.status_code not in (200, 206):
                break
            rows = r.json()
            for x in rows:
                out[x["reference"]] = x["status"]
            if len(rows) < 1000:
                break
    return out


async def upsert(records) -> bool:
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference", json=records,
                             headers={**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"})
            if r.status_code not in (200, 201, 204):
                print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                return False
            return True
    except Exception as e:
        print(f"    ✗ Upsert exception: {e}")
        return False


async def patch_council(cid: int, data: dict):
    async with httpx.AsyncClient(timeout=15) as c:
        await c.patch(f"{SUPABASE_URL}/rest/v1/councils", params={"id": f"eq.{cid}"}, json=data, headers={**_h(), "Prefer": "return=minimal"})


async def geocode(postcodes):
    res = {}
    uniq = list({p.replace(" ", "").upper() for p in postcodes if p})
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(uniq), 100):
            try:
                r = await c.post("https://api.postcodes.io/postcodes", json={"postcodes": uniq[i:i + 100]})
                for it in r.json().get("result", []):
                    if it and it.get("result"):
                        res[it["query"]] = (it["result"]["latitude"], it["result"]["longitude"])
            except Exception as e:
                print(f"    ⚠ geocode batch failed: {e}")
    return res


# ---------------------------------------------------------------- main
async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Wealden scraper")
    print(f"Window {WINDOW_DAYS} days   detail cap {DETAIL_MAX}   DRY_RUN {DRY_RUN}   SUPABASE {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and not (SUPABASE_URL and SUPABASE_KEY):
        sys.exit("Set SUPABASE_URL and SUPABASE_KEY")
    cid = await council_id() if (SUPABASE_URL and SUPABASE_KEY) else None
    existing = await existing_status(cid) if cid else {}
    print(f"[Wealden] council_id={cid}; {len(existing)} existing rows ({sum(1 for s in existing.values() if s != 'pending')} with an outcome)")

    verify = True
    try:
        async with httpx.AsyncClient(timeout=20) as probe:
            await probe.get(BASE)
    except Exception as e:
        if "CERTIFICATE" in str(e).upper() or "issuer" in str(e):
            print("    ℹ certificate chain incomplete - continuing without certificate checks (read-only public data)")
            verify = False

    today = date.today()
    start = today - timedelta(days=WINDOW_DAYS)
    windows, d = [], start
    while d <= today:
        windows.append((d, min(d + timedelta(days=6), today)))
        d += timedelta(days=7)

    rows: dict = {}
    problems = []
    async with httpx.AsyncClient(timeout=60, verify=verify, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}) as c:
        await get(c, BASE + "/Planning/WeeklyList/")   # accept the disclaimer once
        for kind in ("valid", "determined"):
            for a, b in windows:
                try:
                    got = await sweep(c, kind, a, b)
                except Exception as e:
                    problems.append(f"{kind} {a}..{b}: {e}")
                    print(f"    ⚠ {kind} {a}..{b}: {e}")
                    continue
                for ref, row in got.items():
                    rows.setdefault(ref, row)
                    if kind == "determined":
                        rows[ref]["decided"] = row["decided"]
                        rows[ref]["status_text"] = row["status_text"]
                await asyncio.sleep(DELAY)

        skipped_aa = [r for r in rows if r.upper().endswith("/AA")]
        for r in skipped_aa:
            rows.pop(r)
        print(f"\n    ℹ {len(rows)} applications ({len(skipped_aa)} adjoining-authority consultations skipped)")
        status_text_counts = Counter(r["status_text"] for r in rows.values())
        print(f"    ℹ table status text: {dict(status_text_counts.most_common(12))}")

        # outcome for decided applications
        detail_used = 0
        outcome_labels = Counter()
        first_printed = 0
        for ref, row in rows.items():
            st = _status_from_table(row["status_text"])
            row["_state"] = st
            if st != "decided":
                continue
            old = existing.get(ref)
            if old in ("approved", "refused", "withdrawn"):
                row["_outcome"] = old
                continue
            if detail_used >= DETAIL_MAX or (time.monotonic() - START) / 60 > MAX_MINUTES or not row["href"]:
                continue
            detail_used += 1
            try:
                r = await get(c, BASE + row["href"])
            except Exception as e:
                print(f"    ⚠ detail {ref}: {e}")
                continue
            fields = parse_detail_fields(r.text)
            if first_printed < 3:
                first_printed += 1
                print(f"    ℹ detail fields for {ref}: {dict(list(fields.items())[:25])}")
                if first_printed == 1:
                    print("    ℹ detail page text (first 1200 chars): " + re.sub(r'\s+', ' ', BeautifulSoup(r.text, 'html.parser').get_text(' ', strip=True))[:1200])
            raw = outcome_from_fields(fields)
            outcome_labels[raw or "(none found)"] += 1
            row["_outcome"] = _normalise_outcome(raw) if raw else None
            await asyncio.sleep(DELAY)
        print(f"    ℹ detail pages read: {detail_used}; outcome labels: {dict(outcome_labels.most_common(15))}")

    apps = []
    for ref, row in rows.items():
        st = row["_state"]
        if st == "withdrawn":
            status = "withdrawn"
        elif st == "decided":
            status = row.get("_outcome") or "closed"
        else:
            status = "pending"
            if existing.get(ref) in ("approved", "refused", "withdrawn", "closed"):   # never let a stale 'pending' overwrite a known outcome
                status = existing[ref]
        apps.append({
            "reference": ref,
            "address": _title_address(row["location"]),
            "postcode": _postcode(row["location"]),
            "description": _sentence_case(row["description"]),
            "application_type": _app_type(ref),
            "status": status,
            "submitted_date": _dmy(row["valid"]),
            "decision_date": _dmy(row["decided"]),
            "council_url": BASE + row["href"] if row["href"] else None,
        })
    print(f"    ℹ by status: {dict(Counter(a['status'] for a in apps))}")
    print(f"    ℹ with decision date: {sum(1 for a in apps if a['decision_date'])}; without submitted date: {sum(1 for a in apps if not a['submitted_date'])}; no postcode: {sum(1 for a in apps if not a['postcode'])}")

    if DRY_RUN:
        print("\n    --- sample rows ---")
        for a in apps[:6]:
            print("   ", a)
        print("\nDRY_RUN: nothing written.")
        if problems:
            print("⚠ PROBLEMS:\n    " + "\n    ".join(problems))
            sys.exit(1)
        return
    if not apps:
        print("    ✗ nothing found - not saving")
        await patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        sys.exit(1 if problems else 0)

    coords = await geocode([a["postcode"] for a in apps if a["postcode"]])
    print(f"    Geocoded {len(coords)} postcodes")
    for a in apps:
        pc = (a["postcode"] or "").replace(" ", "").upper()
        a["lat"], a["lng"] = coords.get(pc, (None, None))
    have = [(a["lat"], a["lng"]) for a in apps if a["lat"]]
    if have:
        clat, clng = statistics.median(g[0] for g in have), statistics.median(g[1] for g in have)
        n = 0
        for a in apps:
            if not a["lat"]:
                a["lat"], a["lng"], a["geocode_quality"] = clat, clng, "centroid"
                n += 1
        print(f"    Council centroid fallback for {n} apps")
    records = [{"council_id": cid, "source": SOURCE, **{k: v for k, v in a.items()}} for a in apps]
    for r in records:
        for k in ("submitted_date", "decision_date"):
            r[k] = r[k].isoformat() if r.get(k) else None
    saved, ok = 0, True
    for i in range(0, len(records), 50):
        if await upsert(records[i:i + 50]):
            saved += len(records[i:i + 50])
        else:
            ok = False
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        await patch_council(cid, {"coverage_source": SOURCE, "last_scraped_at": now, "last_saved_at": now, "consecutive_empty_runs": 0, "active": True})
        print(f"    ✓ Saved {saved}")
    else:
        print(f"    ⚠ Partial save: {saved} of {len(records)}")
    print(f"\nFinished in {(time.monotonic() - START) / 60:.1f} minutes   applications saved: {saved}")
    if problems or not ok:
        print("⚠ PROBLEMS:\n    " + "\n    ".join(problems or ["partial save"]))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
