#!/usr/bin/env python3
"""
PlanFind - Ribble Valley weekly-list scraper (2026-10-31).

Ribble Valley switched off its online planning search (webportal.ribblevalley.gov.uk, scraped by ribblevalley_scraper.py) and
replaced it with a reference/address search that cannot be listed by date. The council still publishes two weekly PDFs:
  - Planning applications REGISTERED (weekly list, by week ending date)  - download page 235
  - Planning applications DECIDED    (decided list, by week ending date) - download page 249
This scraper reads the newest WEEKS_BACK weeks of both.

Each PDF entry (checked against the real files by ribblevalley_weekly_recon.py) has: reference (3/2026/0309), application type,
address, description, an OS grid reference, officer, plus DATE VALID (registered list) or Decision Date and Decision Type (decided
list; real outcome text such as APPROVED WITH CONDITIONS / REFUSED). The PDF text is in two columns that a plain text dump
interleaves, so the parser works on word positions: a block starts at each reference at the left margin, words left of the
"Development Description:" label are the left column, words right of it are the description and the grid reference.

Order matters: registered lists are saved first (status pending, never overwriting an application that already has an outcome),
decided lists second (status from the decision), so an application ends up with its outcome whichever list it was in.
The OS grid reference is converted to latitude/longitude; postcodes.io is the fallback.
"""
import asyncio
import io
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
import pdfplumber
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from ribblevalley_councils import COUNCIL_DB_IDS

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WEEKS_BACK = int(os.environ.get("WEEKS_BACK", "6"))
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "15"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

COUNCIL_NAME = "Ribble Valley Borough Council"
SITE = "https://www.ribblevalley.gov.uk"
PAGES = {
    "registered": SITE + "/downloads/download/235/weekly-lists-of-planning-applications-registered",
    "decided": SITE + "/downloads/download/249/weekly-lists-of-planning-decisions",
}
BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,
    "accept_downloads": True,
}
START = time.monotonic()
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}

REF_RE = re.compile(r"^3/\d{4}/\d{4}[A-Z]{0,4}$")
DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b")


def minutes() -> float:
    return (time.monotonic() - START) / 60


def log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def parse_date(s: str) -> Optional[str]:
    try:
        return datetime.strptime((s or "").strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def week_ending(href: str) -> Optional[date]:
    """'.../weekly-list-30-october-2026' -> 2026-10-30."""
    m = re.search(r"(\d{1,2})-([a-z]+)-(\d{4})", href.lower())
    if not m or m.group(2) not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        return None


def decision_status(text: str) -> str:
    """Decision Type text -> approved / refused / withdrawn; anything else on the decided list is 'closed' (decided, neutral)."""
    k = (text or "").lower()
    if not k:
        return "closed"
    if "withdraw" in k:
        return "withdrawn"
    if "refus" in k or "reject" in k:
        return "refused"
    if any(x in k for x in ("approv", "grant", "permit", "not required", "no objection", "no observation", "lawful", "split")):
        return "approved"
    return "closed"


# ---------------------------------------------------------------------------
# PDF parsing (word positions)
# ---------------------------------------------------------------------------
def _lines(words: list) -> list:
    """Cluster words into text lines by vertical position."""
    out, cur, cur_top = [], [], None
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if cur_top is None or w["top"] - cur_top > 2.5:
            if cur:
                out.append(cur)
            cur, cur_top = [w], w["top"]
        else:
            cur.append(w)
    if cur:
        out.append(cur)
    return [sorted(l, key=lambda w: w["x0"]) for l in out]


def _text(line: list) -> str:
    return " ".join(w["text"] for w in line).strip()


def parse_block(words: list, kind: str) -> Optional[dict]:
    ref_w = next((w for w in words if REF_RE.match(w["text"]) and w["x0"] < 90), None)
    if not ref_w:
        return None
    # the right-hand column starts at the "Development" of "Development Description:"
    rx = 291.0
    desc_w = next((w for w in words if w["text"] == "Description:"), None)
    if desc_w:
        cand = [w["x0"] for w in words if w["text"] == "Development" and abs(w["top"] - desc_w["top"]) < 3 and w["x0"] < desc_w["x0"]]
        if cand:
            rx = min(cand)
    left = [w for w in words if w["x0"] < rx - 4]
    right = [w for w in words if w["x0"] >= rx - 4]

    # ----- left column
    lines = [_text(l) for l in _lines(left)]
    ward = ""
    first = lines[0] if lines else ""
    if first.startswith(ref_w["text"]):
        ward = first[len(ref_w["text"]):].strip()
    idx = next((i for i, l in enumerate(lines) if l.startswith("Development Address")), None)
    app_type = " ".join(lines[1:idx]).strip() if idx is not None else ""
    addr_parts, dt, officer, dec_type = [], None, "", ""
    phase = "addr"
    for l in lines[(idx + 1 if idx is not None else 1):]:
        if l.startswith("Officer:"):
            officer, phase = l[len("Officer:"):].strip(), "after"
            continue
        if l.startswith("Decision Type:"):
            dec_type = l[len("Decision Type:"):].strip()
            continue
        if phase != "addr":
            continue
        s = l.replace("DATE VALID:", " ").replace("Decision Date:", " ")
        m = DATE_RE.search(s)
        if m:
            dt = dt or m.group(1)
            s = s.replace(m.group(1), " ")
        s = " ".join(s.split())
        if s:
            addr_parts.append(s)
    address = " ".join(addr_parts).strip()

    # ----- right column: grid reference + description
    right_sorted = sorted(right, key=lambda w: (w["top"], w["x0"]))
    nums = [w for w in right_sorted if re.match(r"^\d{5,6}$", w["text"]) and w["top"] < ref_w["top"] + 30][:2]
    drop = {id(w) for w in nums}
    for w in right_sorted:
        if w["text"] in ("Grid", "Reference") and w["top"] < ref_w["top"] + 30:
            drop.add(id(w))
    seen_label = False
    for w in right_sorted:
        if not seen_label and w["text"] == "Description:":
            drop.add(id(w))
            seen_label = True
            prev = [p for p in right_sorted if p["text"] == "Development" and abs(p["top"] - w["top"]) < 3 and p["x0"] < w["x0"]]
            if prev:
                drop.add(id(prev[-1]))
    desc = " ".join(_text(l) for l in _lines([w for w in right_sorted if id(w) not in drop])).strip()
    grid = (int(nums[0]["text"]), int(nums[1]["text"])) if len(nums) == 2 else None

    pc = POSTCODE_RE.search(address.upper())
    return {
        "reference": ref_w["text"], "ward": ward, "application_type": app_type, "address": address,
        "postcode": pc.group(1) if pc else None, "description": desc, "grid": grid, "officer": officer,
        "date": parse_date(dt) if dt else None, "decision_type": dec_type,
    }


def parse_pdf_bytes(data: bytes, kind: str) -> list:
    apps, seen = [], set()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
            foot = next((w["top"] for i, w in enumerate(words) if w["text"] == "Parish" and i and words[i - 1]["text"] == "For"), None)
            limit = min(x for x in (foot, page.height - 20) if x is not None)
            words = [w for w in words if w["top"] < limit - 1]
            refs = sorted((w for w in words if REF_RE.match(w["text"]) and w["x0"] < 90), key=lambda w: w["top"])
            for i, r in enumerate(refs):
                top = r["top"] - 2
                end = refs[i + 1]["top"] - 2 if i + 1 < len(refs) else 1e9
                blk = [w for w in words if top <= w["top"] < end]
                a = parse_block(blk, kind)
                if a and a["reference"] not in seen:
                    seen.add(a["reference"])
                    apps.append(a)
    return apps


# ---------------------------------------------------------------------------
# Supabase / geocoding
# ---------------------------------------------------------------------------
def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}


async def supa_upsert(records: list) -> bool:
    if DRY_RUN or not records:
        return True
    ok = True
    for i in range(0, len(records), 50):
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                                 json=records[i:i + 50], headers={**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"})
            if r.status_code not in (200, 201, 204):
                print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                ok = False
        except Exception as e:
            print(f"    ✗ Upsert exception: {e}")
            ok = False
    return ok


async def existing_outcomes(cid: int) -> set:
    """References that already have a status other than pending (registered lists must not set these back to pending)."""
    if DRY_RUN or not SUPABASE_URL:
        return set()
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{SUPABASE_URL}/rest/v1/planning_applications",
                            params={"council_id": f"eq.{cid}", "status": "neq.pending", "select": "reference", "limit": "5000"}, headers=_h())
            r.raise_for_status()
            return {x["reference"] for x in r.json()}
    except Exception as e:
        log(f"⚠ could not read existing outcomes ({type(e).__name__}); registered applications already decided may show as pending until the decided pass")
        return set()


async def geocode(postcodes: list) -> dict:
    out = {}
    uniq = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    async with httpx.AsyncClient(timeout=20) as c:
        for i in range(0, len(uniq), 100):
            try:
                r = await c.post("https://api.postcodes.io/postcodes", json={"postcodes": uniq[i:i + 100]})
                for it in r.json().get("result", []):
                    if it and it.get("result"):
                        out[it["query"]] = (it["result"]["latitude"], it["result"]["longitude"])
            except Exception as e:
                print(f"    ⚠ geocoding batch failed: {e}")
    return out


_TRANSFORMER = None


def grid_to_latlng(e: int, n: int):
    global _TRANSFORMER
    try:
        if _TRANSFORMER is None:
            from pyproj import Transformer
            _TRANSFORMER = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
        lng, lat = _TRANSFORMER.transform(e, n)
        if 53.5 < lat < 54.4 and -3.2 < lng < -1.9:          # Ribble Valley sits around 53.8-54.0 N, 2.3-2.6 W
            return round(lat, 6), round(lng, 6)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Downloading the lists
# ---------------------------------------------------------------------------
async def fetch_lists(kind: str, cutoff: date, page) -> list:
    """[(week_ending, url, pdf_bytes)] for lists newer than cutoff, newest first."""
    try:
        await page.goto(PAGES[kind], wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        log(f"⚠ {kind}: list page failed: {type(e).__name__}: {str(e)[:100]}")
        return []
    links, seen = [], set()
    anchors = page.locator("a")
    for i in range(await anchors.count()):
        href = await anchors.nth(i).get_attribute("href") or ""
        if "/downloads/file" not in href or href in seen:
            continue
        seen.add(href)
        url = href if href.startswith("http") else SITE + href
        we = week_ending(href)
        if we and we >= cutoff:
            links.append((we, url))
    links.sort(reverse=True)
    log(f"{kind}: {len(links)} lists since {cutoff}")
    out = []
    for we, url in links:
        if minutes() > MAX_MINUTES - 2:
            log("⚠ time budget reached while downloading lists")
            break
        try:
            async with page.expect_download(timeout=40_000) as info:
                try:
                    await page.goto(url, timeout=40_000)
                except Exception:
                    pass
            dl = await info.value
            out.append((we, url, open(await dl.path(), "rb").read()))
        except Exception as e:
            log(f"⚠ {kind} {we}: download failed: {type(e).__name__}: {str(e)[:100]}")
        await asyncio.sleep(1)
    return out


def build_records(apps: list, kind: str, cid: int, url: str, coords: dict, skip: set) -> list:
    recs = []
    for a in apps:
        if kind == "registered" and a["reference"] in skip:
            continue
        ll = grid_to_latlng(*a["grid"]) if a["grid"] else None
        if not ll and a["postcode"]:
            ll = coords.get(a["postcode"].upper().replace(" ", ""))
        r = {
            "council_id": cid, "reference": a["reference"], "address": a["address"] or None, "postcode": a["postcode"],
            "description": a["description"] or None, "application_type": a["application_type"] or None,
            "council_url": url, "lat": ll[0] if ll else None, "lng": ll[1] if ll else None, "source": "ribblevalley_weekly",
        }
        if kind == "registered":
            r["submitted_date"] = a["date"]
            r["status"] = "pending"
        else:
            r["decision_date"] = a["date"]
            r["status"] = decision_status(a["decision_type"])
        recs.append(r)
    return recs


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Ribble Valley weekly-list scraper")
    print(f"Weeks back: {WEEKS_BACK}   Budget: {MAX_MINUTES} min   DRY_RUN: {DRY_RUN}   SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_KEY):
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    cutoff = date.today() - timedelta(weeks=WEEKS_BACK)
    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    lists = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = await (await browser.new_context(**CONTEXT_OPTIONS)).new_page()
        for kind in ("registered", "decided"):
            lists[kind] = await fetch_lists(kind, cutoff, page)
        await browser.close()

    parsed = {}
    for kind in ("registered", "decided"):
        parsed[kind] = []
        for we, url, data in sorted(lists[kind]):
            try:
                apps = parse_pdf_bytes(data, kind)
            except Exception as e:
                log(f"⚠ {kind} {we}: could not read the PDF: {type(e).__name__}: {str(e)[:100]}")
                continue
            miss = sum(1 for a in apps if not a["date"] or not a["address"])
            log(f"{kind} week ending {we}: {len(apps)} applications" + (f" ({miss} with a missing date or address)" if miss else ""))
            if kind == "decided":
                ndec = sum(1 for a in apps if not a["decision_type"])
                if ndec:
                    log(f"⚠ {ndec} decided applications without a decision type")
            parsed[kind].append((we, url, apps))

    allapps = [a for k in parsed for _, _, apps in parsed[k] for a in apps]
    if not allapps:
        print("\nNo applications read from the lists - nothing to save.")
        sys.exit(1)
    for k in ("registered", "decided"):
        for we, url, apps in parsed[k][:1]:
            for a in apps[:2]:
                log(f"sample {k}: {a['reference']} | {a['date']} | {a['application_type']!r} | {a['address']!r} | grid {a['grid']} | "
                    f"{a['decision_type']!r} | {a['description'][:70]!r}")

    coords = await geocode([a["postcode"] for a in allapps if a["postcode"] and not a["grid"]])
    skip = await existing_outcomes(cid)
    saved = 0
    for kind in ("registered", "decided"):
        for we, url, apps in parsed[kind]:
            recs = build_records(apps, kind, cid, url, coords, skip)
            if recs and await supa_upsert(recs):
                saved += len(recs)
    nolatlng = sum(1 for a in allapps if not a["grid"] and not (a["postcode"] and a["postcode"].upper().replace(" ", "") in coords))
    if nolatlng:
        log(f"no coordinates for {nolatlng} applications")
    print(f"\n{'=' * 50}\nFinished in {minutes():.1f} minutes. Applications {'read (dry run)' if DRY_RUN else 'saved'}: {saved if not DRY_RUN else len(allapps)}")
    if saved and not DRY_RUN:
        now = datetime.now(timezone.utc).isoformat()
        async with httpx.AsyncClient(timeout=15) as c:
            await c.patch(f"{SUPABASE_URL}/rest/v1/councils", params={"id": f"eq.{cid}"},
                          json={"coverage_source": "ribblevalley_bespoke", "last_scraped_at": now, "last_saved_at": now, "active": True},
                          headers={**_h(), "Prefer": "return=minimal"})


if __name__ == "__main__":
    asyncio.run(main())
