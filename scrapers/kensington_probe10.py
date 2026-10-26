"""Kensington & Chelsea recon, round 10 (council id 45). Writes nothing.

Established so far (Atlas search app):
  API base            https://www.rbkc.gov.uk/planningsearch/api          (c + "/api" with c = "/planningsearch")
  everywhere qid      "everywhere"
  endpoints           {base}/cases/everywhere?...&sort=1       binary, results = [Case...]
                      {base}/caseCounts/everywhere?...         JSON  {Applications, Decisions, Appeals}
                      {base}/searchMapPoints/everywhere?...    binary (not needed)
  filters             caseType bitmask Application1 Appeal2 Decision4; caseStatus Awaiting1 Granted2 Refused4
                      SplitDecision8 Withdrawn16; dateFrom/dateTo epoch ms; sort 1 Latest 2 Oldest 4 AddressThenStatus
  Case record (little-endian, strings = uint32 length + utf-8):
      byte isEnforcement, byte isDirectMatch, byte isRelatedMatch, uint32 distanceMetres, uint32 localUprnPointId,
      Date latestDate, Date dateRegistered, Date currentStageDate, float64 latitude, float64 longitude,
      string uprn, address, caseType, caseReference, descriptionShort, caseReferenceId, currentStageType,
      currentPlanningState, currentStageStatusName, currentStageDateHeading, currentStageStatusNameLong
  response = uint32 count + that many records.
Unknown: the wire format of Date (readDate), what dateFrom/dateTo select (registered vs decided), inclusive end, and any
result cap. This probe prints the readDate code, then calls the API several ways and decodes the replies.
About 12 requests via ScraperAPI (about 120 credits).
"""
import json
import os
import re
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
CLIENT = f"{SITE}/planningsearch/_build/assets/client-Dj7XTNb0.js"
API = "https://api.scraperapi.com/"
LON = ZoneInfo("Europe/London")


def fetch(url, binary=False):
    params = {"api_key": KEY, "url": url, "country_code": "gb"}
    if binary:
        params["binary_target"] = "true"
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, timeout=150, follow_redirects=True)
    except Exception as e:
        print(f"  ERROR {url}: {str(e)[:150]}")
        return None
    print(f"  GET {url[len(SITE):][:150]} -> HTTP {r.status_code} {len(r.content)}B ({time.time() - t0:.0f}s)")
    return r


def midnight_ms(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=LON).timestamp() * 1000)


# ---------- decoder ----------
class Reader:
    def __init__(self, b, date_kind):
        self.b = b
        self.i = 0
        self.date_kind = date_kind

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

    def string(self):
        n = self.u32()
        if n > 200000:
            raise ValueError("absurd string length")
        self.need(n)
        s = self.b[self.i:self.i + n].decode("utf-8")
        self.i += n
        return s

    def date(self):
        k = self.date_kind
        if k == "f64ms":
            return self.f64()
        if k == "i64ms":
            self.need(8)
            v = struct.unpack_from("<q", self.b, self.i)[0]
            self.i += 8
            return float(v)
        if k == "u32s":
            return float(self.u32()) * 1000.0
        if k == "i64s":
            self.need(8)
            v = struct.unpack_from("<q", self.b, self.i)[0]
            self.i += 8
            return float(v) * 1000.0
        raise ValueError(k)


STR_FIELDS = ["uprn", "address", "caseType", "caseReference", "descriptionShort", "caseReferenceId",
              "currentStageType", "currentPlanningState", "currentStageStatusName", "currentStageDateHeading",
              "currentStageStatusNameLong"]


def decode(b, date_kind):
    r = Reader(b, date_kind)
    n = r.u32()
    if n > 100000:
        raise ValueError("absurd count")
    out = []
    for _ in range(n):
        rec = {
            "isEnforcement": bool(r.u8()), "isDirectMatch": bool(r.u8()), "isRelatedMatch": bool(r.u8()),
            "distanceMetres": r.u32(), "localUprnPointId": r.u32(),
            "latestDate": r.date(), "dateRegistered": r.date(), "currentStageDate": r.date(),
            "latitude": r.f64(), "longitude": r.f64(),
        }
        for f in STR_FIELDS:
            rec[f] = r.string()
        out.append(rec)
    if r.i != len(b):
        raise ValueError(f"{len(b) - r.i} trailing bytes")
    return out


def decode_any(b):
    for kind in ("f64ms", "i64ms", "u32s", "i64s"):
        try:
            recs = decode(b, kind)
        except Exception as e:
            print(f"    date kind {kind}: failed ({str(e)[:60]})")
            continue
        if recs and not (50.0 < recs[0]["latitude"] < 53.0):
            print(f"    date kind {kind}: parsed {len(recs)} records but latitude implausible ({recs[0]['latitude']})")
            continue
        print(f"    date kind {kind}: OK, {len(recs)} records")
        return kind, recs
    return None, []


def d(ms):
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(LON).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ms)


def summarise(kind, recs):
    if not recs:
        return
    for rec in recs[:3]:
        show = dict(rec)
        for k in ("latestDate", "dateRegistered", "currentStageDate"):
            show[k] = d(rec[k])
        print("    " + json.dumps(show, ensure_ascii=False)[:900])
    for f in ("currentPlanningState", "currentStageStatusName", "currentStageStatusNameLong", "caseType", "currentStageType",
              "currentStageDateHeading"):
        print(f"    {f}: {dict(Counter(r[f] for r in recs).most_common(12))}")
    print(f"    dateRegistered range: {d(min(r['dateRegistered'] for r in recs))} .. {d(max(r['dateRegistered'] for r in recs))}")
    print(f"    currentStageDate range: {d(min(r['currentStageDate'] for r in recs))} .. {d(max(r['currentStageDate'] for r in recs))}")
    print(f"    latestDate range: {d(min(r['latestDate'] for r in recs))} .. {d(max(r['latestDate'] for r in recs))}")
    print(f"    unique references: {len(set(r['caseReference'] for r in recs))} of {len(recs)}")
    print(f"    sample references: {[r['caseReference'] for r in recs[:8]]}")
    print(f"    no-coordinate rows: {sum(1 for r in recs if not r['latitude'])}")


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1
    print("\n== 1. readDate / writeDate in the bundle")
    r = fetch(CLIENT)
    if r is not None and r.status_code == 200:
        js = r.text
        for pat in (r"readDate\(\)\{.{0,300}", r"writeDate\([a-z]\)\{.{0,300}", r"readString\(\)\{.{0,900}"):
            m = re.search(pat, js, re.S)
            print("  " + (m.group(0).replace("\n", " ") if m else f"(not found: {pat})"))

    today = date.today()
    print(f"\n== 2. caseCounts (JSON) — last 7 days, by type")
    frm, to = midnight_ms(today - timedelta(days=7)), midnight_ms(today)
    for label, extra in (("all", ""), ("Application only", "&caseType=1"), ("Decision only", "&caseType=4"),
                         ("Appeal only", "&caseType=2")):
        rr = fetch(f"{BASE}/caseCounts/everywhere?dateFrom={frm}&dateTo={to}{extra}&sort=1")
        if rr is not None:
            print(f"    {label}: HTTP {rr.status_code} {rr.text[:300]!r}")

    print("\n== 3. cases (binary) — last 7 days, all types")
    rr = fetch(f"{BASE}/cases/everywhere?dateFrom={frm}&dateTo={to}&sort=1")
    kind = None
    if rr is not None and rr.status_code == 200:
        print(f"    content-type {rr.headers.get('content-type')}; first 64 bytes: {rr.content[:64].hex(' ')}")
        kind, recs = decode_any(rr.content)
        if not kind:
            rb = fetch(f"{BASE}/cases/everywhere?dateFrom={frm}&dateTo={to}&sort=1", binary=True)
            if rb is not None and rb.status_code == 200:
                kind, recs = decode_any(rb.content)
        summarise(kind, recs)

    if kind:
        print("\n== 4. what do the dates select? (same 7 days, by type)")
        for label, extra in (("Application (caseType=1)", "&caseType=1"), ("Decision (caseType=4)", "&caseType=4")):
            rr = fetch(f"{BASE}/cases/everywhere?dateFrom={frm}&dateTo={to}{extra}&sort=1")
            if rr is not None and rr.status_code == 200:
                k2, recs2 = decode_any(rr.content)
                print(f"  -- {label}")
                summarise(k2, recs2)

        print("\n== 5. single-day behaviour and result caps")
        day = today - timedelta(days=3)
        for label, f2, t2 in (("day..day", midnight_ms(day), midnight_ms(day)),
                              ("day..day+1", midnight_ms(day), midnight_ms(day + timedelta(days=1)))):
            rr = fetch(f"{BASE}/cases/everywhere?dateFrom={f2}&dateTo={t2}&sort=1")
            if rr is not None and rr.status_code == 200:
                k3, recs3 = decode_any(rr.content)
                print(f"  -- {label} ({day}): {len(recs3)} records; registered dates "
                      f"{sorted(set(d(r['dateRegistered'])[:10] for r in recs3))}; stage dates "
                      f"{sorted(set(d(r['currentStageDate'])[:10] for r in recs3))}")
        for days in (30, 90, 365):
            rr = fetch(f"{BASE}/cases/everywhere?dateFrom={midnight_ms(today - timedelta(days=days))}&dateTo={midnight_ms(today)}&sort=1")
            if rr is not None and rr.status_code == 200:
                k4, recs4 = decode_any(rr.content)
                print(f"  -- last {days} days: {len(recs4)} records ({len(rr.content)} bytes)")
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
