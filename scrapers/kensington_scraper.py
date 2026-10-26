#!/usr/bin/env python3
"""
PlanFind - Royal Borough of Kensington & Chelsea scraper (Atlas planning portal, binary API via ScraperAPI).

How RBKC's register works (found over 14 probe rounds)
  * www.rbkc.gov.uk sits behind a Cloudflare challenge; only ScraperAPI gets through (plain tier, 10 credits a call).
  * The search app (https://www.rbkc.gov.uk/planningsearch) reads  /planningsearch/api/cases/everywhere?dateFrom=<ms>&dateTo=<ms>&sort=1
    which returns ALL matching cases in one reply, as a little-endian binary stream:
        uint32 count, then per case:
          byte isEnforcement, byte isDirectMatch, byte isRelatedMatch, uint32 distanceMetres, uint32 localUprnPointId,
          date latestDate, date dateRegistered, date currentStageDate   (date = uint64 .NET ticks, & 0x3FFF..; always UTC midnight)
          float64 latitude, float64 longitude,
          11 strings (uint32 length + UTF-8): uprn, address, caseType, caseReference, descriptionShort, caseReferenceId,
          currentStageType, currentPlanningState (Application | Decision | Appeal), currentStageStatusName, currentStageDateHeading,
          currentStageStatusNameLong
    One record per case (its latest state); a case appears in a window when its latestDate falls inside it (both ends inclusive,
    UTC dates). So a single sweep catches new applications and decisions on old ones.
  * ScraperAPI text-decodes the body (Windows-1252 -> UTF-8) and the five byte values Windows-1252 leaves undefined
    (0x81 0x8D 0x8F 0x90 0x9D) come back as U+FFFD. Everything else is exactly recoverable by reversing the decode; the lost bytes
    are repaired from the structure: dates must be UTC midnights (one candidate survives), string lengths must be followed by a valid
    next field, coordinates fall back to postcode geocoding. Every reply is checked end to end (the parse must consume the buffer
    exactly, the count prefix must match, and optionally the JSON /caseCounts total must match) and a window that fails is split.

Env: SCRAPERAPI_KEY, SUPABASE_URL, SUPABASE_KEY (not needed for DRY_RUN), KENSINGTON_COUNCIL_ID (45), DAYS_BACK (30),
SLICE_DAYS (14), CHECK_COUNTS (1 = also call /caseCounts per window, +10 credits each), MAX_MINUTES (20), DRY_RUN (1 = write nothing).
"""
import asyncio
import json
import os
import re
import statistics
import struct
import sys
import time
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from itertools import product
from typing import Optional

import httpx

SITE = "https://www.rbkc.gov.uk"
API_BASE = f"{SITE}/planningsearch/api"
SA_URL = "https://api.scraperapi.com/"
SA_KEY = os.environ.get("SCRAPERAPI_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
COUNCIL_ID = int(os.environ.get("KENSINGTON_COUNCIL_ID", "45") or 45)
DAYS_BACK = int(os.environ.get("DAYS_BACK", "30"))
SLICE_DAYS = int(os.environ.get("SLICE_DAYS", "14"))
CHECK_COUNTS = os.environ.get("CHECK_COUNTS", "1") == "1"
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "20"))
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
SOURCE = "kensington_scraper"
START = time.monotonic()

UNDEF = (0x81, 0x8D, 0x8F, 0x90, 0x9D)          # bytes Windows-1252 cannot decode -> U+FFFD
TICKS_1970 = 621355968000000000
TICKS_DAY = 864_000_000_000
TICKS_MASK = 0x3FFFFFFFFFFFFFFF
CREDITS = Counter()


def minutes() -> float:
    return (time.monotonic() - START) / 60


# ---------------------------------------------------------------------------
# Binary decoder with repair of the bytes ScraperAPI destroys
# ---------------------------------------------------------------------------
class ParseError(ValueError):
    pass


def to_buf(raw: bytes) -> list:
    """Reverse ScraperAPI's text decode: UTF-8 text -> original bytes; U+FFFD -> None (one of UNDEF)."""
    text = raw.decode("utf-8")
    out = []
    for ch in text:
        if ch == "�":
            out.append(None)
        else:
            try:
                out.append(ch.encode("cp1252")[0])
            except UnicodeEncodeError:
                raise ParseError(f"unexpected character U+{ord(ch):04X} in the reply (not a Windows-1252 text decode)")
    return out


def _need(buf, i, n):
    if i + n > len(buf):
        raise ParseError("reply ended mid-record")


def _u32(bs):
    return None if None in bs else struct.unpack("<I", bytes(bs))[0]


def _f64(bs):
    return None if None in bs else struct.unpack("<d", bytes(bs))[0]


def _ticks_to_date(v: int) -> Optional[date]:
    days = (v - TICKS_1970) // TICKS_DAY
    if not (-1000 < days < 100000):
        return None
    d = date(1970, 1, 1) + timedelta(days=days)
    return d if 1990 <= d.year <= 2040 else None


def _date8(bs, stats):
    """8 bytes of .NET ticks -> (date | None). Lost bytes are repaired: the real value is a UTC midnight."""
    unk = [k for k, b in enumerate(bs) if b is None]
    if not unk:
        v = struct.unpack("<Q", bytes(bs))[0] & TICKS_MASK
        d = _ticks_to_date(v)
        stats["dates_ok" if d else "dates_empty"] += 1
        return d
    if len(unk) <= 2:
        cands = set()
        for combo in product(UNDEF, repeat=len(unk)):
            b = list(bs)
            for k, val in zip(unk, combo):
                b[k] = val
            v = struct.unpack("<Q", bytes(b))[0] & TICKS_MASK
            if (v - TICKS_1970) % TICKS_DAY == 0:
                d = _ticks_to_date(v)
                if d and 2000 <= d.year <= 2035:
                    cands.add(d)
        if len(cands) == 1:
            stats["dates_recovered"] += 1
            return cands.pop()
    stats["dates_lost"] += 1
    return None


_PREFER = (0x9D, 0x90, 0x8F, 0x8D, 0x81)    # when a lost byte is ambiguous, prefer the likeliest character (0x9D = right double quote)


def _weird(t: str) -> int:
    return sum(1 for ch in t if ch != " " and unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cn", "Zs", "Zl", "Zp"))


def _text(bs, stats):
    if None not in bs:
        return bytes(bs).decode("utf-8", errors="replace")
    unk = [k for k, b in enumerate(bs) if b is None]
    stats["text_guessed"] += 1
    if len(unk) <= 3:
        best = None
        for rank, combo in enumerate(product(_PREFER, repeat=len(unk))):
            b = list(bs)
            for k, val in zip(unk, combo):
                b[k] = val
            try:
                t = bytes(b).decode("utf-8")
            except UnicodeDecodeError:
                continue
            key = (_weird(t), rank)
            if best is None or key < best[0]:
                best = (key, t)
        if best:
            return best[1]
    return bytes(0x3F if b is None else b for b in bs).decode("utf-8", errors="replace")


MAX_LEN_HI = 15            # strings in this API are well under 4096 bytes


def _prefix_ok(buf, j):
    """buf[j:j+4] looks like the next string's uint32 length prefix (length < 4096, high bytes zero)."""
    return (j + 4 <= len(buf) and buf[j + 2] == 0 and buf[j + 3] == 0
            and buf[j + 1] is not None and buf[j + 1] <= MAX_LEN_HI)


def _header_ok(buf, j):
    return j + 3 <= len(buf) and all(b in (0, 1, None) for b in buf[j:j + 3])


def _read_string(buf, i, last, stats):
    _need(buf, i, 4)
    lo, hi, b2, b3 = buf[i:i + 4]
    if b2 != 0 or b3 != 0 or hi is None or hi > MAX_LEN_HI:
        raise ParseError("bad string length prefix")
    cands = [lo + 256 * hi] if lo is not None else [u + 256 * hi for u in UNDEF]
    good = []
    for L in cands:
        j = i + 4 + L
        if j > len(buf):
            continue
        if last:
            ok = (j == len(buf)) or _header_ok(buf, j)
        else:
            ok = _prefix_ok(buf, j)
        if ok and not any(b is not None and (b < 9 or 13 < b < 32) for b in buf[i + 4:j]):
            good.append(L)
    if not good:
        raise ParseError("string does not fit the surrounding structure")
    if lo is None:
        stats["len_repaired" if len(good) == 1 else "len_ambiguous"] += 1
    L = good[0]
    return _text(buf[i + 4:i + 4 + L], stats), i + 4 + L


STR_FIELDS = ["uprn", "address", "caseType", "caseReference", "descriptionShort", "caseReferenceId",
              "currentStageType", "currentPlanningState", "currentStageStatusName", "currentStageDateHeading",
              "currentStageStatusNameLong"]


def _parse_record(buf, i, stats):
    _need(buf, i, 3 + 4 + 4 + 24 + 16 + 44)
    flags = buf[i:i + 3]
    if not all(b in (0, 1, None) for b in flags):
        raise ParseError("bad record header flags")
    i += 3
    rec = {"isEnforcement": bool(flags[0]) if flags[0] is not None else False}
    i += 8                                        # distanceMetres + localUprnPointId (not used)
    for name in ("latestDate", "dateRegistered", "currentStageDate"):
        rec[name] = _date8(buf[i:i + 8], stats)
        i += 8
    rec["latitude"] = _f64(buf[i:i + 8])
    rec["longitude"] = _f64(buf[i + 8:i + 16])
    if rec["latitude"] is None or rec["longitude"] is None:
        stats["coords_lost"] += 1
    i += 16
    for k, f in enumerate(STR_FIELDS):
        rec[f], i = _read_string(buf, i, k == len(STR_FIELDS) - 1, stats)
    return rec, i


def decode_cases(raw: bytes):
    """-> (records, stats, declared_count). Raises ParseError unless the whole reply parses cleanly."""
    buf = to_buf(raw)
    _need(buf, 0, 4)
    declared = _u32(buf[0:4])
    stats = Counter()
    i, recs = 4, []
    while i < len(buf):
        rec, i = _parse_record(buf, i, stats)
        recs.append(rec)
    if declared is not None and declared != len(recs):
        raise ParseError(f"count prefix {declared} but parsed {len(recs)} records")
    if declared is None:
        stats["count_prefix_lost"] += 1
    return recs, stats, declared


# ---------------------------------------------------------------------------
# Mapping to PlanFind rows
# ---------------------------------------------------------------------------
TYPE_BY_PREFIX = {"PP": "Planning Permission", "LB": "Listed Building Consent", "NMA": "Non-Material Amendment",
                  "CON": "Approval of Conditions", "CL": "Certificate of Lawfulness", "PA": "Prior Approval",
                  "ACV": "Asset of Community Value", "EIA": "EIA Screening / Scoping", "ARB": "Tree Works"}
_STATUS_SEEN: Counter = Counter()
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)


def extract_postcode(addr: str) -> Optional[str]:
    ms = POSTCODE_RE.findall(addr or "")
    return f"{ms[-1][0].upper()} {ms[-1][1].upper()}" if ms else None


def classify(state: str, name: str, long_: str) -> str:
    s = f"{name} {long_}".lower()
    if "withdraw" in s:
        out = "withdrawn"
    elif any(w in s for w in ("unlawful", "not lawful", "refus", "reject", "invalid")):
        out = "refused"
    elif any(w in s for w in ("split", "grant", "approv", "permit", "lawful", "no objection", "consent", "not required",
                              "no further action", "permission")):
        out = "approved"
    elif state == "Application" or any(w in s for w in ("open", "await", "consult", "pending", "valid", "registered")):
        out = "pending"
    elif state == "Appeal":
        out = "refused"
    else:
        out = "closed"
    _STATUS_SEEN[(state, name, long_, out)] += 1
    return out


def to_row(r: dict) -> Optional[dict]:
    ref = (r["caseReference"] or "").strip()
    if not ref or r["isEnforcement"]:
        return None
    state = r["currentPlanningState"]
    status = classify(state, r["currentStageStatusName"], r["currentStageStatusNameLong"])
    prefix = ref.split("/")[0].upper()
    lat, lng = r["latitude"], r["longitude"]
    if lat is None or lng is None or not (51.40 < lat < 51.60 and -0.35 < lng < -0.05):
        lat = lng = None
    decided = state in ("Decision", "Appeal") and status != "pending"
    return {
        "reference": ref, "address": r["address"].strip() or None, "postcode": extract_postcode(r["address"]),
        "description": r["descriptionShort"].strip() or None, "application_type": TYPE_BY_PREFIX.get(prefix),
        "status": status, "submitted_date": r["dateRegistered"].isoformat() if r["dateRegistered"] else None,
        "decision_date": r["currentStageDate"].isoformat() if (decided and state == "Decision" and r["currentStageDate"]) else None,
        "council_url": f"{SITE}/planningsearch/cases/{ref}", "lat": lat, "lng": lng,
        "_prefix": prefix, "_state": state, "_latest": r["latestDate"],
    }


# ---------------------------------------------------------------------------
# ScraperAPI access
# ---------------------------------------------------------------------------
def day_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


async def fetch(client: httpx.AsyncClient, url: str, tries: int = 4) -> bytes:
    last = None
    for n in range(tries):
        try:
            r = await client.get(SA_URL, params={"api_key": SA_KEY, "url": url, "country_code": "gb"})
            cost = r.headers.get("sa-credit-cost") or r.headers.get("x-credits-cost")
            if cost and cost.isdigit():
                CREDITS["credits"] += int(cost)
            CREDITS["calls"] += 1
            if r.status_code == 200:
                return r.content
            last = f"HTTP {r.status_code}: {r.text[:120]!r}"
            if r.status_code in (400, 401, 403):
                break
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        await asyncio.sleep(2 + 3 * n)
    raise RuntimeError(f"fetch failed after {tries} tries: {last}")


async def counts_total(client, a: date, b: date) -> Optional[int]:
    try:
        raw = await fetch(client, f"{API_BASE}/caseCounts/everywhere?dateFrom={day_ms(a)}&dateTo={day_ms(b)}&sort=1")
        j = json.loads(raw.decode("utf-8"))
        return int(j["Applications"]) + int(j["Decisions"]) + int(j["Appeals"])
    except Exception as e:
        print(f"    ⚠ caseCounts failed for {a}..{b}: {e}")
        return None


async def collect(client, a: date, b: date, out: dict, stats: Counter, problems: list):
    if minutes() > MAX_MINUTES:
        problems.append(f"MAX_MINUTES reached before {a}..{b}")
        return
    url = f"{API_BASE}/cases/everywhere?dateFrom={day_ms(a)}&dateTo={day_ms(b)}&sort=1"
    recs, st, declared, err = None, Counter(), None, None
    try:
        raw = await fetch(client, url)
        recs, st, declared = decode_cases(raw)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    expected = await counts_total(client, a, b) if (CHECK_COUNTS and err is None) else None
    bad = err is not None or (expected is not None and expected != len(recs))
    if bad:
        why = err or f"parsed {len(recs)} but /caseCounts says {expected}"
        print(f"    ⚠ {a}..{b}: {why}")
        if a < b:
            mid = a + (b - a) // 2
            await collect(client, a, mid, out, stats, problems)
            await collect(client, mid + timedelta(days=1), b, out, stats, problems)
            return
        if recs is None:
            problems.append(f"{a}: {why}")
            return
        problems.append(f"{a}: {why} (kept the parsed records)")
    stats.update(st)
    for r in recs:
        old = out.get(r["caseReference"])
        if old is None or (r["latestDate"] and (old["latestDate"] is None or r["latestDate"] >= old["latestDate"])):
            out[r["caseReference"]] = r
    print(f"    ℹ {a}..{b}: {len(recs)} cases (declared {declared}, caseCounts {expected}); repairs "
          f"{ {k: v for k, v in st.items() if k != 'dates_ok'} }")


# ---------------------------------------------------------------------------
# Supabase + geocoding (same REST pattern as the other scrapers)
# ---------------------------------------------------------------------------
def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}


async def _upsert(records: list) -> bool:
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
        await c.patch(f"{SUPABASE_URL}/rest/v1/councils", params={"id": f"eq.{cid}"}, json=data,
                      headers={**_h(), "Prefer": "return=minimal"})


async def _council_row(cid: int):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name,system,coverage_source", "id": f"eq.{cid}"}, headers=_h())
        r.raise_for_status()
        return r.json()


async def geocode(postcodes: list) -> dict:
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
async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Kensington & Chelsea scraper ({API_BASE})")
    print(f"Days back {DAYS_BACK}   slice {SLICE_DAYS}d   CHECK_COUNTS {CHECK_COUNTS}   DRY_RUN {DRY_RUN}   "
          f"max {MAX_MINUTES} min   council_id {COUNCIL_ID}")
    print(f"SCRAPERAPI_KEY: {'set' if SA_KEY else 'NOT SET'}   SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not SA_KEY:
        print("ERROR: set SCRAPERAPI_KEY")
        sys.exit(1)
    if not DRY_RUN and not (SUPABASE_URL and SUPABASE_KEY):
        print("ERROR: set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    if SUPABASE_URL and SUPABASE_KEY:
        print(f"    ℹ councils row: {await _council_row(COUNCIL_ID)}")

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=DAYS_BACK)
    out: dict = {}
    stats: Counter = Counter()
    problems: list = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(170.0)) as client:
        a = start
        while a <= today:
            b = min(a + timedelta(days=SLICE_DAYS - 1), today)
            await collect(client, a, b, out, stats, problems)
            a = b + timedelta(days=1)

    rows = [x for x in (to_row(r) for r in out.values()) if x]
    skipped = len(out) - len(rows)
    print(f"\n    ℹ {len(out)} cases decoded, {len(rows)} kept ({skipped} enforcement/blank skipped); "
          f"ScraperAPI calls {CREDITS['calls']}, credits {CREDITS['credits']}")
    print(f"    ℹ repairs: { dict(stats) }")
    print(f"    ℹ by status {dict(Counter(r['status'] for r in rows))}; by state {dict(Counter(r['_state'] for r in rows))}")
    print("    ℹ (state, status name, long name) -> mapped status (count):")
    for (st_, nm, lg, mapped), n in _STATUS_SEEN.most_common(25):
        print(f"        {st_!r:<14} {nm!r:<22} {lg!r:<38} -> {mapped:<9} x{n}")
    print(f"    ℹ reference prefixes: {dict(Counter(r['_prefix'] for r in rows).most_common(20))}")
    print(f"    ℹ application types: {dict(Counter(r['application_type'] for r in rows).most_common(12))}")
    subs = [r["submitted_date"] for r in rows if r["submitted_date"]]
    decs = [r["decision_date"] for r in rows if r["decision_date"]]
    print(f"    ℹ submitted_date range {min(subs) if subs else None} .. {max(subs) if subs else None}; "
          f"decision_date range {min(decs) if decs else None} .. {max(decs) if decs else None}")
    print(f"    ℹ missing: postcode {sum(1 for r in rows if not r['postcode'])}, submitted_date "
          f"{sum(1 for r in rows if not r['submitted_date'])}, description {sum(1 for r in rows if not r['description'])}, "
          f"coordinates {sum(1 for r in rows if r['lat'] is None)}; longest description "
          f"{max((len(r['description'] or '') for r in rows), default=0)}")
    if problems:
        print("    ⚠ PROBLEMS:")
        for p in problems:
            print(f"        {p}")

    if DRY_RUN:
        print("\n    --- 8 sample rows ---")
        for r in rows[:8]:
            print("   ", {k: v for k, v in r.items() if not k.startswith("_")})
        print("\nDRY_RUN: nothing written.")
        return
    if not rows:
        print("    ✗ nothing found - not saving")
        await _patch_council(COUNCIL_ID, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        sys.exit(1)

    need = [r for r in rows if r["lat"] is None]
    coords = await geocode([r["postcode"] for r in need if r["postcode"]])
    for r in need:
        pc = (r["postcode"] or "").replace(" ", "").upper()
        if pc in coords:
            r["lat"], r["lng"] = coords[pc]
    have = [(r["lat"], r["lng"]) for r in rows if r["lat"] is not None]
    clat = statistics.median(g[0] for g in have) if have else 51.4990
    clng = statistics.median(g[1] for g in have) if have else -0.1940
    fb = 0
    for r in rows:
        if r["lat"] is None:
            r["lat"], r["lng"] = clat, clng
            fb += 1
    print(f"    Coordinates: {len(rows) - len(need)} from the register, {len(need) - fb} geocoded, {fb} centroid fallback")

    def rec(r, with_sub):
        d = {"council_id": COUNCIL_ID, "reference": r["reference"], "address": r["address"], "postcode": r["postcode"],
             "description": r["description"], "application_type": r["application_type"], "status": r["status"],
             "decision_date": r["decision_date"], "council_url": r["council_url"], "lat": r["lat"], "lng": r["lng"],
             "source": SOURCE}
        if with_sub:
            d["submitted_date"] = r["submitted_date"]
        return d

    groups = [[rec(r, True) for r in rows if r["submitted_date"]], [rec(r, False) for r in rows if not r["submitted_date"]]]
    saved, ok = 0, True
    for g in groups:
        for i in range(0, len(g), 50):
            if await _upsert(g[i:i + 50]):
                saved += len(g[i:i + 50])
            else:
                ok = False
    now = datetime.now(timezone.utc).isoformat()
    if ok and not problems:
        await _patch_council(COUNCIL_ID, {"coverage_source": SOURCE, "last_scraped_at": now, "last_saved_at": now,
                                          "consecutive_empty_runs": 0, "active": True})
        print(f"    ✓ Saved {saved}")
    else:
        print(f"    ⚠ Partial: saved {saved} of {len(rows)}; {len(problems)} window problem(s)")
        if saved:
            await _patch_council(COUNCIL_ID, {"last_scraped_at": now, "last_saved_at": now, "consecutive_empty_runs": 0})
        sys.exit(1)
    print(f"\nFinished in {minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
