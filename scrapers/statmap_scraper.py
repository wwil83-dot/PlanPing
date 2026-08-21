#!/usr/bin/env python3
"""
PlanFind — statmap.co.uk/horizoNext scraper (2026-08-21).

Covers 2 councils on one shared platform: West Lindsey, East
Staffordshire — see statmap_councils.py for the real, confirmed
evidence backing every design decision here.

ARCHITECTURE: real, confirmed direct URL — no need to click through
"Weekly Lists" tab, fill a search form, or navigate a report list at
all. Just construct {base}/planningapplications/?weeklyListDate=
YYYY-MM-DD for each real Monday in the target window and navigate
directly. Genuinely as simple as agileapplications.co.uk once the
right URL was found.

HONEST LIMITATIONS:
  - Real weekly-list dates are whatever the council has actually
    published (confirmed real dates seen in recon: 2026-08-10,
    2026-08-17 — Monday-anchored). Not every Monday necessarily has a
    real published list — a week with no real content should just
    show an empty/small real result, not an error, and this scraper
    treats that as a legitimate empty result rather than a failure.
  - "Status" (a workflow stage) is deliberately never used for this
    project's own status field — only the real, separate "Decision"
    field is. East Staffordshire's real data appears to include real
    consultation RESPONSES to neighbouring authorities' applications,
    not exclusively East Staffordshire's own directly-decided
    applications — worth keeping in mind if application counts ever
    look unusually high for this council specifically.
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
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeout

from statmap_councils import STATMAP_COUNCILS, COUNCIL_DB_IDS

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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "1"))
WEEKS_BACK   = int(os.environ.get("WEEKS_BACK", "2"))
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "100"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _mondays_back(n: int) -> list[date]:
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    return [this_monday - timedelta(weeks=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
    """Applied ONLY to the real, separate 'decision' field — never to
    'status' (a real workflow stage, e.g. 'Live'). Real confirmed
    vocabulary: 'PENDING' (already maps via the fallthrough below),
    'No Objection' (a real consultation-response value, treated as
    approved-adjacent — East Staffordshire's data includes real
    consultation responses to other authorities' applications, not
    exclusively its own directly-decided ones)."""
    if not s:
        return "pending"
    s = s.lower()
    if "no objection" in s:
        return "approved"
    if any(x in s for x in ("approv", "grant", "permit", "allow")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
    return "pending"


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(cell_text: str) -> str:
    """Real evidence: addresses use \\n separators between parts."""
    parts = [" ".join(p.split()) for p in cell_text.split("\n") if p.strip()]
    return ", ".join(parts)


def _parse_uk_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


_ROW_PARSE_DIAGNOSED: set[str] = set()


def _diagnose_row_parse(council_name: str, headers: list[str], html_snippet: str):
    if council_name in _ROW_PARSE_DIAGNOSED:
        return
    _ROW_PARSE_DIAGNOSED.add(council_name)
    print(f"    [{council_name}] ROW PARSE DIAGNOSTIC: real data-fields found: "
          f"{headers!r}. Response snippet: {html_snippet[:500]!r}")


def _parse_results_grid(html: str, council_name: str) -> list[dict]:
    """Real, confirmed MUI DataGrid structure — real, direct data-field
    attributes on every cell (more reliable than aria-label text, which
    varies slightly between councils). Real row identification: role=
    'row' divs, first one is the header, rest are real data rows with
    a real, permanent numeric data-id."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("div", attrs={"role": "row"})
    if len(rows) < 2:
        _diagnose_row_parse(council_name, [], html)
        return []

    header_fields = [c.get("data-field", "") for c in rows[0].find_all("div", attrs={"role": "columnheader"})]
    if not header_fields:
        _diagnose_row_parse(council_name, [], html)
        return []

    apps = []
    for row in rows[1:]:
        row_id = row.get("data-id", "")
        cells = row.find_all("div", attrs={"role": "cell"})
        cell_by_field = {c.get("data-field", ""): c.get_text("\n", strip=True) for c in cells}

        reference = cell_by_field.get("name", "").strip()
        if not reference:
            continue

        raw_address = cell_by_field.get("address", "")
        address = _clean_address(raw_address)
        postcode = _extract_postcode(address)
        proposal = cell_by_field.get("proposal", "")
        received_date = _parse_uk_date(cell_by_field.get("receivedDate", ""))
        decision_text = cell_by_field.get("decision", "")
        decision_date = _parse_uk_date(cell_by_field.get("decisionDate", ""))

        council_url = None
        if row_id:
            council_url = f"__DETAIL_URL_PLACEHOLDER__/{row_id}"  # filled in by caller with real base_url

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": received_date,
            "status": _normalise_status(decision_text),
            "decision_date": decision_date,
            "row_id": row_id,
        })

    if not apps:
        _diagnose_row_parse(council_name, header_fields, html)

    return apps


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_h())
        r.raise_for_status()
        return r.json()


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


# ---------------------------------------------------------------------------
class StatmapPortal:
    def __init__(self, council_name: str, base_url: str, db_council_id: int):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.db_council_id = db_council_id

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def scrape_week(self, browser: Browser, monday: date) -> list[dict]:
        url = f"{self.base_url}/planningapplications/?weeklyListDate={monday.isoformat()}"
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            self._log(f"⚠ Could not load week {monday.isoformat()}: {e}")
            await context.close()
            return []

        try:
            for label in ["Accept additional cookies", "Accept"]:
                btn = page.get_by_role("button", name=label, exact=False)
                if await btn.count() > 0 and await btn.first.is_visible(timeout=1500):
                    await btn.first.click()
                    await asyncio.sleep(0.5)
                    break
        except Exception:
            pass

        await asyncio.sleep(1.5)  # real, deliberate pause for React
                                    # rendering to finish

        # REAL FIX (2026-08-21) — confirmed directly from captured
        # markup: standard MUI DataGrid pagination, aria-label="next"
        # on the forward button. Unlike agileapplications.co.uk's
        # "Show more" pattern (which ADDS rows to the same table), MUI
        # pagination REPLACES the visible 10 rows on each click — so
        # this parses and accumulates after every single click, rather
        # than waiting until everything's loaded and parsing once.
        MAX_PAGES = 20  # real safety cap — 20 pages x 10 rows = 200,
                          # comfortably above anything seen in recon
                          # (20 was the largest real total)
        all_apps: list[dict] = []
        seen_refs_this_week: set[str] = set()
        page_num = 1
        while page_num <= MAX_PAGES:
            html = await page.content()
            page_apps = _parse_results_grid(html, self.council_name)
            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs_this_week:
                    seen_refs_this_week.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1

            try:
                next_btn = page.locator("button[aria-label='next']")
                if await next_btn.count() == 0:
                    break
                is_disabled = await next_btn.first.get_attribute("disabled")
                if is_disabled is not None:
                    break
                await next_btn.first.click(timeout=5000)
                await asyncio.sleep(1.5)
                page_num += 1
            except Exception:
                break

        if page_num > 1:
            self._log(f"Paginated through {page_num} page(s) to load all real rows")

        await context.close()

        apps = all_apps
        for a in apps:
            if a.get("row_id"):
                a["council_url"] = f"{self.base_url}/planningapplications/{a['row_id']}"
            a.pop("row_id", None)

        self._log(f"Week {monday.isoformat()}: {len(apps)} results")
        return apps


# ---------------------------------------------------------------------------
async def process_council(portal: StatmapPortal, browser: Browser, sem: asyncio.Semaphore):
    async with sem:
        cid = portal.db_council_id
        print(f"\n[{portal.council_name}] (council_id={cid})")

        if should_stop():
            print(f"    [{portal.council_name}] — skipping, time budget reached "
                  f"({elapsed_minutes():.1f} min elapsed)")
            return

        all_apps = []
        seen_refs = set()
        for monday in _mondays_back(WEEKS_BACK):
            if should_stop():
                print(f"    [{portal.council_name}] ⚠ Time budget reached, stopping")
                break
            try:
                week_apps = await portal.scrape_week(browser, monday)
            except Exception as e:
                print(f"    [{portal.council_name}] ✗ Error for week {monday}: {e}")
                continue
            for a in week_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)

        if not all_apps:
            await _supa_patch_council(cid, {"coverage_source": "pending"})
            return

        postcodes = [a["postcode"] for a in all_apps if a.get("postcode")]
        coords = await geocode(postcodes) if postcodes else {}
        if postcodes:
            portal._log(f"Geocoding {len(postcodes)} postcodes…")

        fallback_count = 0
        records = []
        for a in all_apps:
            lat, lng = None, None
            if a.get("postcode"):
                key = a["postcode"].upper().replace(" ", "")
                if key in coords:
                    lat, lng = coords[key]
            if lat is None:
                fallback_count += 1

            records.append({
                "council_id": cid,
                "reference": a["reference"],
                "address": a["address"] or None,
                "postcode": a.get("postcode"),
                "description": a.get("description") or None,
                "status": a["status"],
                "submitted_date": a.get("submitted_date"),
                "decision_date": a.get("decision_date"),
                "council_url": a.get("council_url"),
                "lat": lat,
                "lng": lng,
                "source": "statmap_scraper",
            })

        if fallback_count:
            portal._log(f"Council centroid fallback for {fallback_count} apps")

        if records:
            portal._log(f"Upserting {len(records)} records with council_id={cid}")
            ok = await _supa_upsert(records)
            if ok:
                portal._log(f"✓ Saved {len(records)}")
                await _supa_patch_council(cid, {
                    "coverage_source": "statmap_horizonext",
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                })


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind statmap.co.uk/horizoNext scraper")
    print(f"Weeks back:  {WEEKS_BACK}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder (None) DB id "
              "in statmap_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun statmap_councils.py's INSERT_SQL in Supabase first, then "
              "replace each None above with the real id Supabase assigns.")
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        sem = asyncio.Semaphore(CONCURRENCY)
        portals = [
            StatmapPortal(name, base_url, COUNCIL_DB_IDS[name])
            for name, base_url in STATMAP_COUNCILS
        ]

        await asyncio.gather(*[process_council(p, browser, sem) for p in portals])

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
