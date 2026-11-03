#!/usr/bin/env python3
"""
PlanFind - Carmarthenshire County Council weekly planning lists (Excel).

The council's idox portal is not reachable, but its "Search for a Planning Application" page carries the last four weeks of
  * "the weekly planning list"          (applications registered that week)  - .xlsx
  * "the decided weekly planning list"  (decisions issued that week)         - .xlsx
The file addresses contain a random code (/media/mm4pxmgs/26-30-october.xlsx), so every run reads the page, finds the links,
downloads the files and reads them with openpyxl. Columns are found by their (bilingual) headings, not by position.

Registered lists are saved first (status pending, but never over a row that already has an outcome), decided lists after
(approved / refused / withdrawn / closed).

Env: WEEKS_BACK (default 4), MAX_MINUTES (default 10), DRY_RUN (1 = read and print, write nothing),
     PROXY_URLS (optional, comma separated; only used if the direct request fails)
"""
import asyncio
import io
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
import openpyxl
from bs4 import BeautifulSoup

COUNCIL_NAME = "Carmarthenshire County Council"
COUNCIL_ID = 322
PAGE = "https://www.carmarthenshire.gov.wales/council-services/planning/search-for-a-planning-application/"
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WEEKS_BACK = int(os.environ.get("WEEKS_BACK", "4"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "10"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
PROXIES = [p.strip() for p in os.environ.get("PROXY_URLS", "").split(",") if p.strip()]
START = time.monotonic()


def log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}", flush=True)


def _h() -> dict:
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}


# --------------------------------------------------------------------------- fetching
def fetch(url: str) -> Optional[bytes]:
    try:
        r = httpx.get(url, headers=HEAD, timeout=60, follow_redirects=True)
        if r.status_code == 200:
            return r.content
        log(f"HTTP {r.status_code} for {url[-50:]}")
    except Exception as e:
        log(f"direct request failed for {url[-50:]}: {type(e).__name__}")
    if PROXIES:
        from curl_cffi import requests as cr
        for i, px in enumerate(PROXIES[:4]):
            try:
                r = cr.get(url, impersonate="chrome", proxies={"http": px, "https": px}, timeout=90)
                if r.status_code == 200:
                    log(f"fetched via proxy {i}")
                    return r.content
            except Exception as e:
                log(f"proxy {i} failed: {type(e).__name__}")
    return None


def find_files(html: bytes) -> list:
    """[(kind, label, url)] for every spreadsheet link; kind is 'registered' or 'decided'."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        if not re.search(r"\.xlsx?($|\?)", a["href"], re.I):
            continue
        url = urljoin(PAGE, a["href"])
        if url in seen:
            continue
        seen.add(url)
        label = a.get_text(" ", strip=True)
        out.append(("decided" if "decided" in label.lower() else "registered", label, url))
    return out


# --------------------------------------------------------------------------- reading a workbook
def _date(v) -> Optional[str]:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(v or ""))
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def _postcode(text: str) -> Optional[str]:
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", (text or "").upper())
    return m.group(1) if m else None


def _col(headers: list, *want, avoid=()):
    for i, h in enumerate(headers):
        if h and all(w in h for w in want) and not any(a in h for a in avoid):
            return i
    return None


def week_range(ws) -> Optional[tuple]:
    for row in ws.iter_rows(min_row=1, max_row=8, values_only=True):
        for c in row:
            m = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", str(c or ""))
            if m:
                return _date(m.group(1)), _date(m.group(2))
    return None


def read_workbook(data: bytes, kind: str, label: str) -> list:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    ws = wb.worksheets[0]
    hdr_row = None
    for r_i, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), start=1):
        cells = [re.sub(r"\s+", " ", str(c)).strip().lower() for c in row if c not in (None, "")]
        # the real header row has several short cells, one of them "... applic no"; the title rows have one long cell
        if len(cells) >= 4 and any(re.search(r"applic\w*\.? ?no\b", c) and len(c) < 60 for c in cells):
            hdr_row = r_i
            break
    if hdr_row is None:
        log(f"⚠ {label}: no header row ('Applic No' with several columns) found in the first 15 rows")
        return []
    headers = [re.sub(r"\s+", " ", str(c)).strip().lower() if c else "" for c in next(ws.iter_rows(min_row=hdr_row, max_row=hdr_row, values_only=True))]
    n = max((i for i, h in enumerate(headers) if h), default=-1) + 1
    headers = headers[:n]
    ix = {
        "ref": _col(headers, "applic", "no"),
        "link": _col(headers, "weblink"),
        "reg": _col(headers, "registration", "date") if _col(headers, "registration", "date") is not None else _col(headers, "dyddiad cofrestru"),
        "type": _col(headers, "application type"),
        "addr": _col(headers, "site address"),
        "desc": _col(headers, "proposal"),
        "status": _col(headers, "status", avoid=("detailed", "manwl")),
        "detail": _col(headers, "detailed status"),
        "ddate": _col(headers, "decision", "date") if _col(headers, "decision", "date") is not None else _col(headers, "decided", "date"),
        "decision": _col(headers, "decision", avoid=("date",)),
    }
    if True:
        log(f"{label}: header row {hdr_row}; columns {[h[:40] for h in headers]}")
    out = []
    for r_i, row in enumerate(ws.iter_rows(min_row=hdr_row + 1, max_row=ws.max_row), start=hdr_row + 1):
        def val(k):
            i = ix.get(k)
            return row[i].value if i is not None and i < len(row) else None
        ref = str(val("ref") or "").strip()
        if not re.fullmatch(r"[A-Za-z]{1,4}/[\w/.\-]{1,24}", ref):
            continue
        link = None
        if ix["link"] is not None and ix["link"] < len(row):
            cell = row[ix["link"]]
            link = cell.hyperlink.target if cell.hyperlink and cell.hyperlink.target else None
        addr = re.sub(r"\s+", " ", str(val("addr") or "")).strip()
        out.append({
            "ref": ref, "link": link, "address": addr, "postcode": _postcode(addr),
            "description": re.sub(r"\s+", " ", str(val("desc") or "")).strip() or None,
            "type": str(val("type") or "").strip() or None,
            "reg_date": _date(val("reg")),
            "decision_date": _date(val("ddate")),
            "status": str(val("status") or "").strip(),
            "detail": str(val("detail") or "").strip(),
            "decision": str(val("decision") or "").strip(),
        })
    return out


def decided_status(a: dict) -> tuple:
    """(status, label used). Looks at decision / detailed status / status, in that order."""
    for label in (a.get("decision"), a.get("detail"), a.get("status")):
        key = (label or "").lower()
        if not key:
            continue
        if "withdraw" in key:
            return "withdrawn", label
        if any(x in key for x in ("refus", "reject")):
            return "refused", label
        if any(x in key for x in ("grant", "approv", "permit", "no objection", "not required", "lawful", "consent", "split decision")) \
                and "not approved" not in key and "not granted" not in key:
            return "approved", label
    return "closed", a.get("decision") or a.get("detail") or a.get("status") or ""


# --------------------------------------------------------------------------- database
async def existing_non_pending(c: httpx.AsyncClient) -> set:
    got, start = set(), 0
    while True:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/planning_applications", headers={**_h(), "Range": f"{start}-{start + 999}"},
                        params={"select": "reference", "council_id": f"eq.{COUNCIL_ID}", "status": "neq.pending"})
        if r.status_code not in (200, 206):
            log(f"⚠ could not read existing outcomes (HTTP {r.status_code}); registered rows may reset a pending status")
            return got
        rows = r.json()
        got.update(x["reference"] for x in rows)
        if len(rows) < 1000:
            return got
        start += 1000


async def geocode(c: httpx.AsyncClient, postcodes: list) -> dict:
    out = {}
    uniq = list({p.upper().replace(" ", "") for p in postcodes if p})
    for i in range(0, len(uniq), 100):
        try:
            r = await c.post("https://api.postcodes.io/postcodes", json={"postcodes": uniq[i:i + 100]}, timeout=30)
            for item in r.json().get("result", []):
                if item and item.get("result"):
                    out[item["query"]] = (item["result"]["latitude"], item["result"]["longitude"])
        except Exception as e:
            log(f"⚠ geocoding batch failed: {type(e).__name__}")
    return out


async def upsert(c: httpx.AsyncClient, records: list) -> int:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    groups = {}
    for rec in records:
        groups.setdefault(tuple(sorted(rec)), []).append(rec)
    saved = 0
    for rows in groups.values():
        for i in range(0, len(rows), 50):
            chunk = rows[i:i + 50]
            for attempt in range(1, 4):
                try:
                    r = await c.post(f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                                     json=chunk, headers=headers, timeout=90)
                    if r.status_code in (200, 201, 204):
                        saved += len(chunk)
                        break
                    log(f"✗ upsert HTTP {r.status_code}: {r.text[:200]}")
                except Exception as e:
                    log(f"✗ upsert attempt {attempt}: {type(e).__name__}: {e!r}")
                await asyncio.sleep(3 * attempt)
    return saved


# --------------------------------------------------------------------------- main
async def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Carmarthenshire weekly-list scraper")
    print(f"Weeks back: {WEEKS_BACK}   Budget: {MAX_MINUTES:g} min   DRY_RUN: {DRY_RUN}   SUPABASE: {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")
    if not DRY_RUN and not (SUPABASE_URL and SUPABASE_KEY):
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set")
        return 1

    html = fetch(PAGE)
    if not html:
        print("ERROR: the planning page could not be fetched")
        return 1
    files = find_files(html)
    log(f"{len(files)} spreadsheet links on the page: " + "; ".join(f"{k}: {l[:28]}" for k, l, _ in files))
    if not files:
        print("ERROR: no spreadsheet links found")
        return 1

    cutoff = date.today() - timedelta(weeks=WEEKS_BACK, days=7)
    registered, decided = [], []
    for kind, label, url in files:
        if (time.monotonic() - START) / 60 > MAX_MINUTES:
            log("⚠ time budget reached")
            break
        data = fetch(url)
        if not data:
            log(f"⚠ could not download {label}")
            continue
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
            wr = week_range(wb.worksheets[0])
            if wr and wr[1] and date.fromisoformat(wr[1]) < cutoff:
                log(f"skipping {kind} list {wr[0]}..{wr[1]} (older than {WEEKS_BACK} weeks)")
                continue
            rows = read_workbook(data, kind, f"{kind} {label[:20]}")
        except Exception as e:
            log(f"⚠ cannot read {label}: {type(e).__name__}: {e}")
            continue
        log(f"{kind} list {wr[0] + '..' + wr[1] if wr else label[:20]}: {len(rows)} applications")
        if rows and len(wb.worksheets) > 1:
            log(f"note: workbook has {len(wb.worksheets)} sheets, read the first ({wb.worksheets[0].title!r})")
        (decided if kind == "decided" else registered).extend(rows)

    if not registered and not decided:
        print("\nNothing read.")
        return 1

    # one record per reference; a decided row beats a registered one
    recs = {}
    for a in registered:
        recs[a["ref"]] = {"a": a, "decided": False}
    for a in decided:
        old = recs.get(a["ref"])
        if old and not a["reg_date"]:
            a["reg_date"] = old["a"]["reg_date"]
        recs[a["ref"]] = {"a": a, "decided": True}

    labels = Counter()
    async with httpx.AsyncClient(timeout=60) as c:
        keep_status = set() if DRY_RUN or not SUPABASE_URL else await existing_non_pending(c)
        geo = await geocode(c, [r["a"]["postcode"] for r in recs.values()])
        pts = list(geo.values())
        centre = (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts)) if pts else (None, None)
        rows_out, fallback = [], 0
        for ref, r in recs.items():
            a = r["a"]
            ll = geo.get((a["postcode"] or "").upper().replace(" ", ""))
            if not ll:
                fallback += 1
                ll = centre
            if r["decided"]:
                status, lab = decided_status(a)
                labels[lab] += 1
                dd = a["decision_date"]
            else:
                if ref in keep_status:
                    continue
                status, dd = "pending", None
            rec = {
                "council_id": COUNCIL_ID, "reference": ref, "address": a["address"] or None, "postcode": a["postcode"],
                "description": a["description"], "application_type": a["type"], "status": status,
                "council_url": a["link"] or PAGE, "lat": ll[0], "lng": ll[1], "source": "carmarthenshire_scraper",
            }
            if a["reg_date"]:
                rec["submitted_date"] = a["reg_date"]
            if dd:
                rec["decision_date"] = dd
            rows_out.append(rec)

        log(f"decision labels seen: {labels.most_common(12)}")
        log(f"{len(rows_out)} records; status {dict(Counter(x['status'] for x in rows_out))}; "
            f"with decision date {sum(1 for x in rows_out if x.get('decision_date'))}; centroid fallback {fallback}")
        for x in rows_out[:3] + [y for y in rows_out if y["status"] != "pending"][:3]:
            log(f"sample: {x['reference']} | {x.get('submitted_date')} | {x.get('decision_date')} | {x['status']} | {x['application_type']!r} | "
                f"{x['address']!r} | {x['lat']},{x['lng']} | {str(x['description'])[:50]!r}")
        if DRY_RUN:
            print(f"\nDRY RUN: nothing saved ({len(rows_out)} records)")
            return 0
        saved = await upsert(c, rows_out)
        log(f"✓ saved {saved} of {len(rows_out)}")
        now = datetime.now(timezone.utc).isoformat()
        patch = {"coverage_source": "carmarthenshire_weekly_lists", "last_scraped_at": now, "active": True}
        if saved:
            patch["last_saved_at"] = now
        await c.patch(f"{SUPABASE_URL}/rest/v1/councils", params={"id": f"eq.{COUNCIL_ID}"}, json=patch,
                      headers={**_h(), "Prefer": "return=minimal"})
    print(f"\n{'=' * 50}\nFinished in {(time.monotonic() - START) / 60:.1f} minutes. Applications saved: {saved}")
    return 0 if saved or not rows_out else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
