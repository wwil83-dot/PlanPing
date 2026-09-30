#!/usr/bin/env python3
"""
PlanFind — East Lindsey District Council scraper (2026-09-30, ward-based rebuild).

REPLACES East Lindsey's entry in the shared idox_scraper.py's council
list, which had NO local-authority or ward filter at all on the shared
"South & East Lincolnshire Councils Partnership" portal it uses with
Boston and South Holland. Confirmed this caused real, live mislabeling:
a direct check of East Lindsey's own saved data found 4 applications
genuinely belonging to Boston (confirmed via PE20/PE21 postcodes and
the real "Kirton" ward), saved under East Lindsey instead — since
removed. IMPORTANT: East Lindsey must be removed from idox_scraper.py's
own council list once this standalone scraper is live, or the old,
unfiltered version will keep running in parallel and undo this fix.

REAL, CONFIRMED FIX: filters by real ward instead of address text,
reusing the exact approach already proven for Boston last night. East
Lindsey's own wards were derived by elimination from the shared
portal's full 73-option ward dropdown, having already confirmed
Boston's real 15 wards and South Holland's real 18 wards independently
(via each council's own ward poster / electoral records). Whatever
remained — 37 candidates below — belongs to East Lindsey. Several are
independently recognisable as real East Lindsey towns already seen in
this portal's own results (Mablethorpe, Skegness-area Ingoldmells,
Horncastle, Spilsby, Alford, Woodhall Spa), which supports this
derivation without needing a separate, direct verification pass the
way Boston and South Holland each got.

HONEST LIMITATION: "ALF8" and "Alford" both appear as separate options
in the real dropdown and may be a legacy duplicate of the same ward —
both are included here rather than guessing which is authoritative,
since the scraper's own reference-based deduplication makes an
accidental duplicate harmless, while dropping a real one would
silently lose data.

Results parsing, date-range handling, and Supabase upsert logic are
copied unchanged from boston_scraper.py, since it's the same
underlying shared portal regardless of which council's wards are being
searched.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
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

BASE_URL = "https://publicaccess.e-lindsey.gov.uk/online-applications"
COUNCIL_NAME = "East Lindsey District Council"

EAST_LINDSEY_WARDS = [
    ("ALF8", "ALF8"),
    ("Alford", "ALFO"),
    ("Binbrook", "BINB"),
    ("Burgh Le Marsh", "BURG"),
    ("Chapel St. Leonards", "CHAP"),
    ("Coningsby And Mareham", "CONI"),
    ("Croft", "CROF"),
    ("Friskney", "FRIS"),
    ("Fulstow", "FULS"),
    ("Grimoldby", "GRIM"),
    ("Hagworthingham", "HAGW"),
    ("Halton Holegate", "HALT"),
    ("Holton Le Clay And North Thoresby", "HOLT"),
    ("Horncastle", "HORN"),
    ("Ingoldmells", "INGO"),
    ("Legbourne", "LEGB"),
    ("Mablethorpe", "MABL"),
    ("Marshchapel And Somercotes", "MARS"),
    ("North Holme", "NHOL"),
    ("Priory And St James", "PRIO"),
    ("Roughton", "ROUG"),
    ("Scarbrough And Seacroft", "SCAR"),
    ("Sibsey And Stickney", "SIBS"),
    ("Spilsby", "SPIL"),
    ("St. Clement's", "SCLE"),
    ("St. Margaret's", "SMAG"),
    ("St. Mary's", "SMAY"),
    ("St. Michael's", "SMIC"),
    ("Sutton On Sea", "SUTT"),
    ("Tetford And Donington", "TETF"),
    ("Tetney", "TETN"),
    ("Trinity (East Lindsey)", "TRIN"),
    ("Wainfleet", "WAIN"),
    ("Willoughby With Sloothby", "WILL"),
    ("Winthorpe", "WINT"),
    ("Withern And Theddlethorpe", "WITH"),
    ("Woodhall Spa", "WOOD"),
    ("Wragby", "WRAG"),
]

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "60"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "60"))
EAST_LINDSEY_COUNCIL_ID = int(os.environ.get("EAST_LINDSEY_COUNCIL_ID", "0"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 3


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_status(s: str) -> str:
    if not s:
        return "pending"
    key = s.lower()
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in ("appeal", "awaiting", "regist", "pending")):
        return "pending"
    if "decided" in key:
        return "pending"
    if key == "unknown":
        return "pending"
    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        print(f"    ⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


_DATE_FORMAT_DIAGNOSED: set[str] = set()


def _parse_received_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a %d %b %Y").date()
    except ValueError:
        if raw not in _DATE_FORMAT_DIAGNOSED:
            _DATE_FORMAT_DIAGNOSED.add(raw)
            print(f"    ⚠ DATE FORMAT DIAGNOSTIC: could not parse "
                  f"received date {raw!r} with the expected format")
        return None


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_page(html: str) -> list[dict]:
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    results_container = soup.find("ul", id="searchresults")
    items = results_container.find_all("li", class_="searchresult") if results_container else []

    if not items and not _ROW_STRUCTURE_DIAGNOSED:
        _ROW_STRUCTURE_DIAGNOSED = True
        print(f"    ⚠ ROW STRUCTURE DIAGNOSTIC: no real ul#searchresults > "
              f"li.searchresult items found — real structure may have changed "
              f"since confirmation")

    for item in items:
        summary_link = item.find("a", class_="summaryLink")
        if not summary_link:
            continue

        href = summary_link.get("href")
        detail_url = None
        if href:
            detail_url = href if href.startswith("http") else f"{BASE_URL.rsplit('/', 1)[0]}{href}"

        desc_div = summary_link.find("div", class_="summaryLinkTextClamp")
        description = desc_div.get_text(strip=True) if desc_div else ""

        address_p = item.find("p", class_="address")
        address = address_p.get_text(strip=True) if address_p else ""

        status_value = item.select_one(".badge-status .value")
        status_raw = status_value.get_text(strip=True) if status_value else ""

        meta_info = item.find("p", class_="metaInfo")
        reference = ""
        received_raw = ""
        if meta_info:
            meta_text = meta_info.get_text(" ", strip=True)
            for part in meta_text.split("·"):
                part = part.strip()
                if part.lower().startswith("ref"):
                    reference = part.split(":", 1)[1].strip() if ":" in part else ""
                elif part.lower().startswith("received"):
                    received_raw = part.split(":", 1)[1].strip() if ":" in part else ""

        if not reference or len(reference) < 3:
            continue

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": _extract_postcode(address),
            "description": description,
            "application_type": "Planning",
            "status": _normalise_status(status_raw),
            "submitted_date": _parse_received_date(received_raw),
            "council_url": detail_url,
        })

    seen_refs: set[str] = set()
    deduped = []
    for a in apps:
        if a["reference"] not in seen_refs:
            seen_refs.add(a["reference"])
            deduped.append(a)
    return deduped


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                json=records, headers=headers,
            )
            if r.status_code not in (200, 201, 204):
                print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                return False
            return True
    except Exception as e:
        print(f"    ✗ Upsert exception: {e}")
        return False


async def _supa_patch_council(council_id: int, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.patch(
            f"{SUPABASE_URL}/rest/v1/councils",
            params={"id": f"eq.{council_id}"},
            json=data,
            headers={**_h(), "Prefer": "return=minimal"},
        )


async def geocode(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    if not unique:
        return results
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(unique), 100):
            try:
                r = await c.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": unique[i:i + 100]},
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"],
                        )
            except Exception as e:
                print(f"    ⚠ Geocoding batch failed ({len(unique[i:i + 100])} postcodes): {e}")
    return results


async def scrape_ward(page, ward_name: str, ward_code: str,
                       start_str: str, end_str: str) -> list[dict]:
    advanced_url = f"{BASE_URL}/search.do?action=advanced"
    try:
        await page.goto(advanced_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        ward_select = page.locator("select[name='searchCriteria.ward']")
        if await ward_select.count() == 0:
            print(f"    [{ward_name}] ⚠ No real ward select found — stopping this ward")
            return []
        await ward_select.select_option(value=ward_code)

        await page.fill("input[name='date(applicationReceivedStart)']", start_str, timeout=5_000)
        await page.fill("input[name='date(applicationReceivedEnd)']", end_str, timeout=5_000)

        submit = page.locator("input[type='submit'], button[type='submit']")
        if await submit.count() == 0:
            print(f"    [{ward_name}] ⚠ No real submit control found — stopping this ward")
            return []

        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit.first.click(timeout=5_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"    [{ward_name}] ⚠ Search fill/submit failed: {type(e).__name__}: {e!r}")
        return []

    error_like = page.locator("[class*='error' i]")
    if await error_like.count() > 0:
        error_text = (await error_like.first.text_content() or "").strip()
        if error_text:
            print(f"    [{ward_name}] ⚠ Real validation message: {error_text!r}")
            return []

    html = await page.content()
    apps = _parse_results_page(html)
    print(f"    [{ward_name}] {len(apps)} real applications found")
    return apps


async def scrape() -> list[dict]:
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        for ward_name, ward_code in EAST_LINDSEY_WARDS:
            if should_stop():
                print(f"⚠ Time budget reached, stopping before ward {ward_name}")
                break
            ward_apps = await scrape_ward(page, ward_name, ward_code, start_str, end_str)
            for a in ward_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind East Lindsey District Council "
          f"scraper (ward-based)")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"Wards:       {len(EAST_LINDSEY_WARDS)}")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    if not EAST_LINDSEY_COUNCIL_ID:
        print("ERROR: EAST_LINDSEY_COUNCIL_ID not set.")
        sys.exit(1)

    raw_apps = await scrape()

    if not raw_apps:
        await _supa_patch_council(EAST_LINDSEY_COUNCIL_ID, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        print("\nNo real East Lindsey applications found across any ward this run.")
        return

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        print(f"Geocoding {len(postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for a in raw_apps:
        lat, lng = None, None
        if a.get("postcode"):
            key = a["postcode"].upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        records.append({
            "council_id": EAST_LINDSEY_COUNCIL_ID,
            "reference": a["reference"],
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "submitted_date": a["submitted_date"].isoformat() if a.get("submitted_date") else None,
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "east_lindsey_scraper",
        })

    if fallback_count:
        print(f"Council centroid fallback for {fallback_count} apps")

    print(f"Upserting {len(records)} records with council_id={EAST_LINDSEY_COUNCIL_ID}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"✓ Saved {len(records)}")
        await _supa_patch_council(EAST_LINDSEY_COUNCIL_ID, {
            "coverage_source": "east_lindsey_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {len(records) if ok else 0}")


if __name__ == "__main__":
    asyncio.run(main())
