"""Kensington & Chelsea recon, round 11 (council id 45). Writes nothing.

Round 10 results:
  * https://www.rbkc.gov.uk/planningsearch/api/caseCounts/everywhere?dateFrom=<ms>&dateTo=<ms>&sort=1 -> JSON
    {"Applications":179,"Appeals":2,"Decisions":111} for the last 7 days (caseType does not change the counts).
  * .../api/cases/everywhere?... -> a binary body whose first uint32 is 0x124 = 292 = 179+111+2, i.e. ONE record per case
    covering all three kinds. ScraperAPI's plain endpoint returned it as text/html utf-8 and mangled bytes >= 0x80
    (cp1252 -> utf-8, lossy). binary_target=true answered 400.
  * Date wire format: .NET ticks:  ms = ((uint64 & 0x3FFFFFFFFFFFFFFF) - 621355968000000000) / 10000.

This round tries to get the body byte-exact:
   A  ScraperAPI proxy mode (proxy-server.scraperapi.com:8001, verify off)
   B  ScraperAPI API endpoint with binary_target=true (prints the error body)
   C  ScraperAPI API endpoint with keep_headers=true and Accept: application/json (maybe the API can answer JSON)
and, with the first method that decodes, prints samples, status mix, what the date range selects and result caps.
"""
import base64
import json
import os
import struct
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

KEY = os.environ.get("SCRAPERAPI_KEY", "")
SITE = "https://www.rbkc.gov.uk"
BASE = f"{SITE}/planningsearch/api"
API = "https://api.scraperapi.com/"
PROXY = f"http://scraperapi.country_code=gb:{KEY}@proxy-server.scraperapi.com:8001"
LON = ZoneInfo("Europe/London")
TICKS_1970 = 621355968000000000
MASK = 0x3FFFFFFFFFFFFFFF


def midnight_ms(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=LON).timestamp() * 1000)


def d(ms):
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(LON).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ms)


# ---------------- decoder (reused by the scraper) ----------------
class Reader:
    def __init__(self, b):
        self.b, self.i = b, 0

    def need(self, n):
        if self.i + n > len(self.b):
            raise ValueError("short buffer")

    def u8(self):
        self.need(1)
        v = self.b[self.i]
        self.i += 1
        return v

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.b, self.i)[0]
        self.i += 4
        return v

    def f64(self):
        self.need(8)
        v = struct.unpack_from("<d", self.b, self.i)[0]
        self.i += 8
        return v

    def date_ms(self):
        self.need(8)
        v = struct.unpack_from("<Q", self.b, self.i)[0]
        self.i += 8
        return ((v & MASK) - TICKS_1970) / 10000.0

    def string(self):
        n = self.u32()
        if n > 500000:
            raise ValueError("absurd string length")
        self.need(n)
        s = self.b[self.i:self.i + n].decode("utf-8")
        self.i += n
        return s


STR_FIELDS = ["uprn", "address", "caseType", "caseReference", "descriptionShort", "caseReferenceId",
              "currentStageType", "currentPlanningState", "currentStageStatusName", "currentStageDateHeading",
              "currentStageStatusNameLong"]


def decode_cases(b):
    r = Reader(b)
    n = r.u32()
    if n > 200000:
        raise ValueError("absurd count")
    out = []
    for _ in range(n):
        rec = {"isEnforcement": bool(r.u8()), "isDirectMatch": bool(r.u8()), "isRelatedMatch": bool(r.u8()),
               "distanceMetres": r.u32(), "localUprnPointId": r.u32(),
               "latestDate": r.date_ms(), "dateRegistered": r.date_ms(), "currentStageDate": r.date_ms(),
               "latitude": r.f64(), "longitude": r.f64()}
        for f in STR_FIELDS:
            rec[f] = r.string()
        out.append(rec)
    if r.i != len(b):
        raise ValueError(f"{len(b) - r.i} trailing bytes")
    return out


# ---------------- transports ----------------
def get_A(url):
    with httpx.Client(proxy=PROXY, verify=False, timeout=150, follow_redirects=True) as c:
        return c.get(url)


def get_B(url):
    return httpx.get(API, params={"api_key": KEY, "url": url, "country_code": "gb", "binary_target": "true"},
                     timeout=150, follow_redirects=True)


def get_C(url):
    return httpx.get(API, params={"api_key": KEY, "url": url, "country_code": "gb", "keep_headers": "true"},
                     headers={"Accept": "application/json"}, timeout=150, follow_redirects=True)


METHODS = {"A": get_A, "B": get_B, "C": get_C}


def attempt(method, url, label=""):
    t0 = time.time()
    try:
        r = METHODS[method](url)
    except Exception as e:
        print(f"  [{method}] {label} ERROR {str(e)[:200]}")
        return None
    print(f"  [{method}] {label} HTTP {r.status_code} {len(r.content)}B content-type={r.headers.get('content-type')} ({time.time() - t0:.0f}s)")
    return r


def summarise(recs):
    if not recs:
        return
    for rec in recs[:3]:
        show = dict(rec)
        for k in ("latestDate", "dateRegistered", "currentStageDate"):
            show[k] = d(rec[k])
        print("    " + json.dumps(show, ensure_ascii=False)[:1000])
    for f in ("currentPlanningState", "currentStageStatusName", "currentStageStatusNameLong", "caseType",
              "currentStageType", "currentStageDateHeading"):
        print(f"    {f}: {dict(Counter(r[f] for r in recs).most_common(14))}")
    for f in ("dateRegistered", "currentStageDate", "latestDate"):
        print(f"    {f} range: {d(min(r[f] for r in recs))} .. {d(max(r[f] for r in recs))}")
    print(f"    unique references {len(set(r['caseReference'] for r in recs))} of {len(recs)};"
          f" sample {[r['caseReference'] for r in recs[:8]]}")
    print(f"    no-coordinates {sum(1 for r in recs if not r['latitude'])}; empty uprn {sum(1 for r in recs if not r['uprn'])}"
          f"; empty description {sum(1 for r in recs if not r['descriptionShort'])}")
    by = Counter()
    for r in recs:
        by[(r["currentPlanningState"], r["currentStageStatusName"])] += 1
    print(f"    state x status: {dict(by.most_common(20))}")


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1
    today = date.today()
    frm, to = midnight_ms(today - timedelta(days=2)), midnight_ms(today)
    url = f"{BASE}/cases/everywhere?dateFrom={frm}&dateTo={to}&sort=1"

    print("\n== 1. get the body byte-exact")
    winner, recs = None, []
    for m in ("A", "B", "C"):
        r = attempt(m, url, "cases 2 days")
        if r is None:
            continue
        if r.status_code != 200:
            print(f"    body: {r.text[:300]!r}")
            continue
        head = r.content[:48]
        print(f"    first 48 bytes: {head.hex(' ')}")
        if r.content[:1] in (b"{", b"["):
            print(f"    looks like JSON: {r.text[:600]!r}")
            continue
        try:
            recs = decode_cases(r.content)
            print(f"    DECODED {len(recs)} records")
            winner = m
            break
        except Exception as e:
            print(f"    decode failed: {e}")
            print(f"    base64 of first 600 bytes: {base64.b64encode(r.content[:600]).decode()}")
    print(f"  winning transport: {winner}")
    if not winner:
        print("\nNo byte-exact transport yet.")
        return 0

    call = METHODS[winner]

    def cases(f, t, extra=""):
        r = call(f"{BASE}/cases/everywhere?dateFrom={f}&dateTo={t}{extra}&sort=1")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return decode_cases(r.content)

    print("\n== 2. last 7 days: samples and mix")
    f7, t7 = midnight_ms(today - timedelta(days=7)), midnight_ms(today)
    r7 = cases(f7, t7)
    print(f"  {len(r7)} records (caseCounts said 179+111+2 = 292 earlier in the day)")
    summarise(r7)

    print("\n== 3. what does the date range select? single days")
    day = today - timedelta(days=3)
    for label, a, b in (("day..day", midnight_ms(day), midnight_ms(day)),
                        ("day..day+1", midnight_ms(day), midnight_ms(day + timedelta(days=1)))):
        rr = cases(a, b)
        print(f"  -- {label} ({day}): {len(rr)} records")
        print(f"     registered dates {dict(Counter(d(r['dateRegistered'])[:10] for r in rr).most_common(6))}")
        print(f"     stage dates      {dict(Counter(d(r['currentStageDate'])[:10] for r in rr).most_common(6))}")
        print(f"     latest dates     {dict(Counter(d(r['latestDate'])[:10] for r in rr).most_common(6))}")
        print(f"     states           {dict(Counter(r['currentPlanningState'] for r in rr))}")

    print("\n== 4. result caps")
    for days in (30, 90):
        t0 = time.time()
        try:
            rr = cases(midnight_ms(today - timedelta(days=days)), midnight_ms(today))
            print(f"  last {days} days: {len(rr)} records ({time.time() - t0:.0f}s); unique refs {len(set(r['caseReference'] for r in rr))}")
        except Exception as e:
            print(f"  last {days} days: {e}")
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
