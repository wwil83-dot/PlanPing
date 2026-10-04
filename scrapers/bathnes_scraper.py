#!/usr/bin/env python3
"""
PlanFind — Bath & North East Somerset Council scraper (2026-10-04).

HOW IT WORKS (all confirmed by real diagnostic output):
  The Weekly List tab at app.bathnes.gov.uk/webforms/planning/search.html
  has two dropdowns — select#weeklyListOption (Validated / Decided) and
  select#weeklyListBetween (a list of weeks) — and button#weeklySearchBtn.
  Pressing it makes the page POST to
  .../PlanningAPI/v2/planningdata/search/ and the response is a complete
  JSON array for that week (62-86 records per week seen, no paging).
  This scraper drives the page exactly as a person would and reads the
  response the PAGE receives; it does not construct its own API requests.

NOTE ON ROBOTS: api.bathnes.gov.uk disallows automated access in its
robots.txt. Reading the page's own response is still automated access
to that API, so this is a decision the project owner made knowingly,
with a deliberately tiny footprint (a handful of searches every few
days), not a way around the rule.

CONFIRMED FACTS THAT SHAPE THE CODE:
  - The FIRST week option is always the coming week (empty by design);
    weeks are therefore chosen by comparing each option's start date
    with today, never by fixed position.
  - After one search the URL stays at ...#weeklyList; a goto to a URL
    that differs only by hash is a same-document jump with NO reload,
    leaving the results view on screen and the dropdowns hidden. Every
    search uses a FRESH page for this reason.
  - Validated is filtered on the record's `isharedate` (when it synced
    to the council's GIS), not the validation date. They matched on
    every record seen, but a record could land in a neighbouring week,
    so runs always cover several overlapping weeks.
  - Decided is filtered on application_decided_from/to — a genuinely
    different list, not a copy of Validated.
  - Records carry real latitude/longitude; postcode geocoding is only
    a fallback.
  - `parish_text` looked UNRELIABLE (a Bathwick address showed "Chew
    Magna", another "Midsomer Norton") so it is not used. `ward_text`
    looked right but isn't stored either.

  - Each result row on the results screen links to
    ./details.html?refval=<url-encoded reference> (target=_blank — i.e.
    the site itself opens it as a fresh load in a new tab), so
    council_url is built from the reference. Not independently opened
    from outside the site; the evidence is the site's own link.

NOT YET CONFIRMED:
  - The status vocabulary beyond the 16 codes seen in the first 8-week
    run (see STATUS_BY_CODE). The dropdown lists ~41 statuses, so a code
    not yet seen falls back to keywords and is logged once.
  - Whether the API caps results: counts of 62-86 showed no cap, and a
    warning prints if a week ever returns a suspiciously round number.
  - Whether `proposal` is complete or truncated API-side (the diagnostic
    cut its own display at 90 chars); the run prints the longest
    description seen so a hard cap would be visible.
"""
import asyncio
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,
}

URL = "https://app.bathnes.gov.uk/webforms/planning/search.html#weeklyList"
COUNCIL_NAME = "Bath and North East Somerset Council"
# Each result row links to ./details.html?refval=<url-encoded reference>
# (target=_blank, so it is a normal fresh load in a new tab).
DETAIL_URL = "https://app.bathnes.gov.uk/webforms/planning/details.html?refval={}"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "25"))
WEEKS_BACK = int(os.environ.get("WEEKS_BACK", "3"))   # complete-or-current weeks, newest first
BATHNES_COUNCIL_ID = int(os.environ.get("BATHNES_COUNCIL_ID", "0"))

START_TIME = time.monotonic()
STATUSES = ("Validated", "Decided")
WEEK_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+to\s+(\d{2})/(\d{2})/(\d{4})")
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b")
SUSPICIOUS_COUNTS = {100, 200, 250, 500, 1000}


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------- parsing

def clean_text(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def clean_address(raw, fallback=None) -> str:
    """`address` is newline/CR separated (and sometimes ends in a stray
    CR); `addressline` is the comma-joined version. Rebuild from the
    former, fall back to the latter."""
    parts = [p.strip() for p in re.split(r"[\r\n]+", str(raw or "")) if p.strip()]
    if parts:
        return ", ".join(parts)
    return clean_text(fallback)


def extract_postcode(text: str) -> Optional[str]:
    m = POSTCODE_RE.search((text or "").upper())
    return m.group(1) if m else None


def parse_iso_date(s) -> Optional[str]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10]).date().isoformat()
    except ValueError:
        return None


def valid_coords(lat, lng) -> bool:
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if lat == 0 and lng == 0:
        return False
    return 49.5 <= lat <= 61.0 and -8.7 <= lng <= 2.0


# Confirmed from a real 8-week run (1,208 raw records, 16 distinct codes).
# Exact codes are used first because they are stable; keywords are only a
# fallback for codes not yet seen (the dropdown lists ~41 statuses).
#
# JUDGEMENT CALLS (flip any of these if you disagree):
#   approved  <- NOOBJ "No Objection", CON "Consent", DISCHG "Condition
#                Discharged", APP "Approve", LAWFUL "Lawful", EXEMPT
#                "Exempt from consent", AN "Prior Approval NOT Required":
#                all mean the application got a positive outcome or the
#                works may proceed. Not all are literally "planning
#                permission granted", but showing them as Pending would
#                wrongly suggest a decision is still awaited.
#   pending   <- SPLIT (part granted, part refused), NOCOM "No Comment",
#                NONDET "Non-determination", CLO (blank): genuinely
#                unclear, so left as pending rather than guessed.
STATUS_BY_CODE = {
    "PERMIT": "approved", "APP": "approved", "CON": "approved",
    "NOOBJ": "approved", "LAWFUL": "approved", "DISCHG": "approved",
    "EXEMPT": "approved", "AN": "approved",
    "RF": "refused",
    "WD": "withdrawn",
    "PCO": "pending", "PDE": "pending",
    "SPLIT": "pending", "NOCOM": "pending", "NONDET": "pending", "CLO": "pending",
}

POSITIVE_KEYWORDS = ("permitted", "permission", "approve", "granted", "consent",
                     "no objection", "discharged", "exempt", "not required", "agreed")

_STATUS_DIAGNOSED: set[str] = set()


def normalise_status(code, status_text, pending_flag) -> str:
    """Pending flag first, then the exact code, then keywords for codes
    not yet seen (logged once so the table can be extended)."""
    if str(pending_flag) == "1":
        return "pending"
    code_key = clean_text(code).upper()
    if code_key in STATUS_BY_CODE:
        return STATUS_BY_CODE[code_key]
    key = clean_text(status_text).lower()
    if not key:
        return "pending"
    if key.startswith("appeal"):
        return "pending"            # an appeal's outcome isn't stated
    if "refus" in key:
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in POSITIVE_KEYWORDS) or re.search(r"\blawful\b", key):
        return "approved"
    if "pending" in key:
        return "pending"
    marker = f"{code_key}|{key}"
    if marker not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(marker)
        print(f"    ⚠ STATUS DIAGNOSTIC: unrecognised status code {code!r} "
              f"({status_text!r}) — filed as 'pending'")
    return "pending"


def parse_record(rec: dict) -> Optional[dict]:
    reference = clean_text(rec.get("refval"))
    if len(reference) < 3:
        return None
    address = clean_address(rec.get("address"), rec.get("addressline"))
    lat, lng = rec.get("latitude"), rec.get("longitude")
    ok = valid_coords(lat, lng)
    return {
        "reference": reference,
        "address": address,
        "postcode": extract_postcode(address),
        "description": clean_text(rec.get("proposal")),
        "application_type": clean_text(rec.get("dcapptyp_text")) or clean_text(rec.get("dcapptyp")) or "Planning",
        "status": normalise_status(rec.get("dcstat"), rec.get("dcstat_text"), rec.get("pending")),
        "submitted_date": parse_iso_date(rec.get("dateaprecv")) or parse_iso_date(rec.get("dateapval")),
        "council_url": DETAIL_URL.format(quote(reference, safe="")),
        "lat": float(lat) if ok else None,
        "lng": float(lng) if ok else None,
    }


def parse_week_label(label: str):
    m = WEEK_RE.search(label or "")
    if not m:
        return None
    d1, m1, y1, d2, m2, y2 = (int(x) for x in m.groups())
    return date(y1, m1, d1), date(y2, m2, d2)


def select_weeks(labels: list[str], today: date, n: int) -> list[str]:
    """Weeks whose START is on or before today (so the coming week is
    skipped), newest first, at most n."""
    dated = []
    for label in labels:
        rng = parse_week_label(label)
        if rng and rng[0] <= today:
            dated.append((rng[0], label))
    dated.sort(reverse=True)
    return [label for _, label in dated[:n]]


# --------------------------------------------------------------- supabase

def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"}


async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                json=records, headers=headers)
            if r.status_code not in (200, 201, 204):
                print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                return False
            return True
    except Exception as e:
        print(f"    ✗ Upsert exception: {e}")
        return False


async def _supa_patch_council(council_id: int, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.patch(f"{SUPABASE_URL}/rest/v1/councils", params={"id": f"eq.{council_id}"},
                      json=data, headers={**_h(), "Prefer": "return=minimal"})


async def geocode(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    if not unique:
        return results
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(unique), 100):
            try:
                r = await c.post("https://api.postcodes.io/postcodes",
                                 json={"postcodes": unique[i:i + 100]})
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (item["result"]["latitude"],
                                                  item["result"]["longitude"])
            except Exception as e:
                print(f"    ⚠ Geocoding batch failed ({len(unique[i:i + 100])} postcodes): {e}")
    return results


# --------------------------------------------------------------- scraping

async def settle(page, extra=1.5):
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def read_week_labels(context) -> list[str]:
    page = await context.new_page()
    try:
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        await settle(page)
        opts = await page.locator("select#weeklyListBetween option").all_text_contents()
        return [o.strip() for o in opts if o.strip()]
    finally:
        await page.close()


async def fetch_week(context, week_label: str, status_word: str) -> Optional[list]:
    """One real search on a FRESH page; returns the raw JSON list the
    page received, or None if the search failed."""
    page = await context.new_page()
    try:
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        await settle(page)

        # Status first, then the week — re-reading the week options on
        # THIS page and choosing by label, so a newly added week can't
        # shift an index.
        await page.locator("select#weeklyListOption").select_option(label=status_word, timeout=8_000)
        await settle(page, 1.5)
        week = page.locator("select#weeklyListBetween")
        labels = [o.strip() for o in await week.locator("option").all_text_contents()]
        if week_label not in labels:
            print(f"    [{week_label}/{status_word}] ⚠ week no longer offered — skipping")
            return None
        await week.select_option(index=labels.index(week_label), timeout=8_000)
        await settle(page, 1.0)

        async with page.expect_response(
            lambda r: "planningdata/search" in r.url and r.request.method == "POST",
            timeout=30_000,
        ) as info:
            await page.locator("button#weeklySearchBtn").click(timeout=8_000)
        resp = await info.value
        if resp.status != 200:
            print(f"    [{week_label}/{status_word}] ⚠ search returned HTTP {resp.status}")
            return None
        data = await resp.json()
        if not isinstance(data, list):
            print(f"    [{week_label}/{status_word}] ⚠ unexpected response type "
                  f"{type(data).__name__} — expected a list")
            return None
        return data
    except Exception as e:
        print(f"    [{week_label}/{status_word}] ⚠ search failed: {type(e).__name__}: {str(e)[:200]}")
        return None
    finally:
        await page.close()


async def scrape():
    """Returns (merged parsed records by reference, status inventory, failed, attempted)."""
    validated: dict[str, dict] = {}
    decided: dict[str, dict] = {}
    inventory: Counter = Counter()
    failed = attempted = 0
    longest_desc = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)

        labels = await read_week_labels(context)
        weeks = select_weeks(labels, date.today(), WEEKS_BACK)
        print(f"Weeks offered: {len(labels)}; fetching {len(weeks)}: {weeks}\n")

        for week_label in weeks:
            for status_word in STATUSES:
                if should_stop():
                    print("⚠ Time budget reached — stopping early")
                    break
                attempted += 1
                raw = await fetch_week(context, week_label, status_word)
                if raw is None:
                    failed += 1
                    continue
                note = ""
                if len(raw) in SUSPICIOUS_COUNTS:
                    note = "  ⚠ round number — possible result cap"
                print(f"    [{week_label}/{status_word}] {len(raw)} records{note}")
                target = validated if status_word == "Validated" else decided
                for rec in raw:
                    inventory[(rec.get("dcstat"), clean_text(rec.get("dcstat_text")),
                               str(rec.get("pending")))] += 1
                    longest_desc = max(longest_desc, len(clean_text(rec.get("proposal"))))
                    parsed = parse_record(rec)
                    if parsed:
                        target[parsed["reference"]] = parsed
                await asyncio.sleep(2)   # politeness gap between searches

        await context.close()
        await browser.close()

    # Decided carries the later state of an application, so it wins when
    # the same reference appears in both lists.
    merged = {**validated, **decided}
    print(f"\nLongest description seen: {longest_desc} chars "
          f"(a hard cap would show as many values stuck at one length)")
    return merged, inventory, failed, attempted


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind {COUNCIL_NAME} scraper")
    print(f"Weeks back:  {WEEKS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    if not BATHNES_COUNCIL_ID:
        print("ERROR: BATHNES_COUNCIL_ID not set.")
        sys.exit(1)

    merged, inventory, failed, attempted = await scrape()

    if attempted and failed == attempted:
        print(f"\nERROR: all {attempted} searches failed — the page or its API may have changed.")
        sys.exit(1)

    print("\nStatus inventory (code | text | pending flag -> count):")
    for (code, text, pend), n in inventory.most_common(30):
        print(f"    {code!s:10} | {text:45} | pending={pend:5} -> {n}")

    if not merged:
        await _supa_patch_council(BATHNES_COUNCIL_ID, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()})
        print("\nNo applications found this run.")
        return

    need_geocode = [a["postcode"] for a in merged.values()
                    if a["lat"] is None and a["postcode"]]
    coords = await geocode(need_geocode) if need_geocode else {}

    records, no_coord = [], 0
    for a in merged.values():
        lat, lng = a["lat"], a["lng"]
        if lat is None and a["postcode"]:
            lat, lng = coords.get(a["postcode"].replace(" ", ""), (None, None))
        if lat is None:
            no_coord += 1
        records.append({
            "council_id": BATHNES_COUNCIL_ID,
            "reference": a["reference"],
            "address": a["address"] or None,
            "postcode": a["postcode"],
            "description": a["description"] or None,
            "application_type": a["application_type"],
            "status": a["status"],
            "submitted_date": a["submitted_date"],
            "council_url": a["council_url"],
            "lat": lat,
            "lng": lng,
            "source": "bathnes_scraper",
        })

    api_coords = sum(1 for a in merged.values() if a["lat"] is not None)
    print(f"\nCoordinates: {api_coords} from the council's own data, "
          f"{len(records) - api_coords - no_coord} from postcode geocoding, "
          f"{no_coord} with none (left null)")
    print(f"Upserting {len(records)} records with council_id={BATHNES_COUNCIL_ID}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"✓ Saved {len(records)}")
        await _supa_patch_council(BATHNES_COUNCIL_ID, {
            "coverage_source": "bathnes_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat()})

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {len(records) if ok else 0}")


if __name__ == "__main__":
    asyncio.run(main())
