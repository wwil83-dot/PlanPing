#!/usr/bin/env python3
"""
PlanFind Civica Portal360 scraper — Playwright edition.

Built 2026-07-24 from real recon evidence (civica_recon.py +
civica_stalbans_results_recon.py) against St Albans, the first
confirmed-working Civica Portal360 council. See civica_councils.py's
module docstring for how confirmed councils get added.

ARCHITECTURE — genuinely simpler than Arcus, closer to Idox:
  - Portal360 results are reached via a DIRECT, CONSTRUCTIBLE URL with
    date-range query parameters (civica.query.{field}From/To) — CONFIRMED
    real evidence: navigating straight to such a URL renders real results
    immediately, no form-fill-and-click interaction needed at all (unlike
    Arcus's Salesforce Lightning forms).
  - Results are NOT in a <table> — they're knockout.js-rendered
    <li class="civica-keyobjectlistitem"> items inside a
    <div class="civicakeyobjectlist">. Each item's reference+date is in
    an <a class="civica-pbdc-internetdesc"> ("Planning Application {REF}
    - Valid From {DATE}"), address in a
    <div class="civica-pbdc-uprndisplay">, description in a
    <div class="civica-pbdc-proposal">.
  - Pagination is 10-per-page with a real "Next" button
    (data-bind="click: onForward") — no URL page-number parameter
    confirmed, so this clicks through rather than guessing one.

HONEST LIMITATIONS in this v1, worth remembering:
  - The query URL SYNTAX (civica.query.{field}From/To, camelCase) is
    CONFIRMED — pulled directly from a real "decision_date" link in
    St Albans' own homepage HTML. The scraper actually queries by
    "received_date" though (see CivicaPortal.scrape's comment for why) —
    that specific FIELD NAME is extrapolated from the visible form label
    "Received Date (From)", applying the confirmed syntax pattern, not
    independently confirmed itself. A diagnostic prints real evidence
    (page title + body snippet) if a query returns zero results, so a
    wrong guess surfaces clearly on the first real run rather than
    silently returning nothing forever.
  - status is deliberately left as "pending" for every record in this
    v1. The results-list view does NOT show decision outcome
    (approved/refused) anywhere in the summary text — only a reference,
    a "Valid From" date, address, and proposal — and that date is always
    the SUBMISSION date, never a decision date, regardless of which
    query filtered it. Querying by received_date (submission) rather
    than decision_date keeps this self-consistent: newly-submitted
    applications defaulting to pending is a reasonable, honest default.
    Getting the real approved/refused/withdrawn status would need
    visiting each application's own detail page (a client-side
    "#VIEW?RefType=PBDC&KeyNo=..." route within the SPA, not yet
    investigated) — future work, not guessed here.
  - Only ONE council (St Albans) is confirmed working. Waverley — the
    other Portal360 example found — is confirmed BLOCKED by a real
    Incapsula WAF (see civica_councils.py comment). Any new council
    needs the same recon-first discipline as Arcus before being added.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeout,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "30"))
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "2"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "14"))
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "20"))  # safety cap — 10/page,
                                                         # so 200 results max
                                                         # per council per run

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 3


# ---------------------------------------------------------------------------
# Helpers — field parsing (Civica Portal360-specific formats seen in recon)
# ---------------------------------------------------------------------------
_ITEM_TEXT_RE = re.compile(
    r"Planning Application\s+(?P<ref>.+?)\s*-\s*Valid From\s+(?P<date>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _build_search_url(base_url: str, date_field: str, date_from: date, date_to: date) -> str:
    """Builds a direct Portal360 search URL — CONFIRMED working for
    'decision_date' (pulled from a real link in St Albans' own homepage
    HTML). Other date_field values are untested extrapolation."""
    df = quote(date_from.strftime("%d/%m/%Y"), safe="")
    dt = quote(date_to.strftime("%d/%m/%Y"), safe="")
    return (f"{base_url.rstrip('/')}/search-applications"
            f"?civica.query.{date_field}From={df}&civica.query.{date_field}To={dt}")


# ---------------------------------------------------------------------------
# Supabase REST API — identical to idox_scraper.py/arcus_scraper.py, same
# table/schema
# ---------------------------------------------------------------------------
def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_h()
        )
        r.raise_for_status()
        return r.json()


async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/planning_applications"
                f"?on_conflict=council_id,reference",
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


async def _supa_increment_empty_runs(council_id: int):
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            await c.post(
                f"{SUPABASE_URL}/rest/v1/rpc/increment_empty_runs",
                json={"council_id_param": council_id},
                headers={**_h(), "Prefer": "return=minimal"},
            )
        except Exception as e:
            print(f"    ⚠ Failed to increment empty-run counter: {e}")


# ---------------------------------------------------------------------------
# Geocoding — identical to idox_scraper.py/arcus_scraper.py
# ---------------------------------------------------------------------------
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
# CivicaPortal
# ---------------------------------------------------------------------------
class CivicaPortal:
    def __init__(self, council_name: str, base_url: str, db_council_id: int):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.db_council_id = db_council_id

    async def _scrape_one_query(self, page: Page, date_field: str,
                                 date_from: date, date_to: date) -> list[dict]:
        url = _build_search_url(self.base_url, date_field, date_from, date_to)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"    ⚠ [{self.council_name}] Navigation error ({date_field}): {e}")
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        # Portal360 uses knockout.js client-side rendering — give it time
        # to actually populate the results list after network idle.
        await asyncio.sleep(3)

        items = page.locator("li.civica-keyobjectlistitem")
        all_apps = []
        seen_refs_this_query: set[str] = set()

        for page_num in range(1, MAX_PAGES + 1):
            try:
                await page.wait_for_selector("li.civica-keyobjectlistitem", timeout=10_000)
            except PlaywrightTimeout:
                # DIAGNOSTIC (2026-07-24): zero results is either genuine
                # (nothing in this date range — plausible for a single
                # week) or a wrong query-parameter guess. Print real
                # evidence so this is distinguishable, same principle as
                # every diagnostic built for Idox/Arcus.
                title = await page.title()
                body_snippet = ""
                try:
                    body_text = await page.locator("body").inner_text()
                    body_snippet = " ".join(body_text.split())[:300]
                except Exception:
                    pass
                print(f"    ⚠ [{self.council_name}] No results for '{date_field}' "
                      f"{date_from} to {date_to} — title: {title!r}, "
                      f"body: {body_snippet!r}")
                break

            count = await items.count()
            for i in range(count):
                try:
                    item_text = await items.nth(i).locator(".civica-pbdc-internetdesc").inner_text()
                except Exception:
                    continue
                m = _ITEM_TEXT_RE.search(item_text)
                if not m:
                    continue
                ref = m.group("ref").strip()
                if ref in seen_refs_this_query:
                    continue
                seen_refs_this_query.add(ref)

                try:
                    address = await items.nth(i).locator(".civica-pbdc-uprndisplay").inner_text()
                except Exception:
                    address = ""
                try:
                    proposal = await items.nth(i).locator(".civica-pbdc-proposal").inner_text()
                except Exception:
                    proposal = ""

                all_apps.append({
                    "reference":        ref,
                    "address":          address.strip(),
                    "postcode":         _extract_postcode(address),
                    "description":      proposal.strip(),
                    "application_type": "",
                    # HONEST LIMITATION (2026-07-24): see module docstring
                    # — decision outcome isn't shown in this summary view
                    # at all, only a "Valid From" date. Defaulting to
                    # pending rather than guessing an outcome we haven't
                    # actually seen displayed anywhere.
                    "status":           "pending",
                    "submitted_date":   _parse_date(m.group("date")),
                    "decision_date":    None,
                    "council_url":      self.base_url,
                    "source":           "civica_scraper",
                })

            # Try to paginate — real "Next" button confirmed via recon,
            # no URL page-number parameter confirmed so this clicks
            # through rather than guessing one.
            next_btn = page.locator("div.btn.secondary-btn", has_text="Next")
            if await next_btn.count() == 0:
                break
            classes = await next_btn.first.get_attribute("class") or ""
            if "disabled-btn" in classes:
                break
            try:
                await next_btn.first.click(timeout=5_000)
                await asyncio.sleep(2)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1)
            except Exception:
                break

        return all_apps

    async def scrape(self, browser: Browser) -> list[dict]:
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
            locale="en-GB",
            ignore_https_errors=True,
        )
        page = await context.new_page()

        today = date.today()
        date_from = today - timedelta(days=DAYS_BACK)

        # SELF-CORRECTION (2026-07-24): originally queried by
        # "decision_date" here, since that field's exact query-parameter
        # SYNTAX is the one confirmed working (pulled from a real link in
        # St Albans' own homepage HTML). But that would be actively wrong
        # data — querying by decision_date means these applications WERE
        # decided, yet every record defaults to status="pending" (see the
        # module docstring — the summary view never shows the actual
        # outcome). A decided application marked pending is a real
        # contradiction, not just an approximation. Querying by
        # "received_date" instead is self-consistent: newly-submitted
        # applications defaulting to pending is a reasonable, honest
        # default, and it matches the "Valid From" date that's actually
        # displayed in the results (that field is the submission date
        # regardless of which query filtered it — never a decision date).
        #
        # The exact param NAME here ("received_date") is extrapolated
        # from the visible form field label "Received Date (From)",
        # applying the confirmed camelCase From/To syntax pattern proven
        # for decision_date — NOT independently confirmed itself. The
        # diagnostic in _scrape_one_query will surface it clearly on the
        # first real run if this guess is wrong.
        apps = await self._scrape_one_query(page, "received_date", date_from, today)

        await context.close()
        return apps


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def process_council(portal: CivicaPortal, browser: Browser, sem: asyncio.Semaphore) -> int:
    async with sem:
        cid = portal.db_council_id
        print(f"\n[{portal.council_name}] (council_id={cid})")

        if should_stop():
            print(f"    — skipping, time budget reached ({elapsed_minutes():.1f} min elapsed)")
            return "TIME_BUDGET_SKIP"

        try:
            apps = await portal.scrape(browser)
        except Exception as e:
            print(f"    ✗ Error: {e}")
            return 0

        if not apps:
            await _supa_patch_council(cid, {
                "last_scraped_at": datetime.now(timezone.utc).isoformat()
            })
            await _supa_increment_empty_runs(cid)
            return 0

        try:
            need = [a["postcode"] for a in apps if a.get("postcode")]
            if need:
                print(f"    Geocoding {len(set(need))} postcodes…")
                coords = await geocode(need)
                for app in apps:
                    if app.get("postcode"):
                        pc = app["postcode"].strip().upper().replace(" ", "")
                        if pc in coords:
                            app["lat"], app["lng"] = coords[pc]

            records = [{
                "council_id":       cid,
                "reference":        a["reference"],
                "address":          a.get("address"),
                "postcode":         a.get("postcode"),
                "lat":              a.get("lat"),
                "lng":              a.get("lng"),
                "description":      a.get("description"),
                "application_type": a.get("application_type"),
                "status":           a.get("status", "pending"),
                "submitted_date":   a.get("submitted_date"),
                "decision_date":    a.get("decision_date"),
                "council_url":      a.get("council_url"),
                "source":           "civica_scraper",
            } for a in apps]

            print(f"    Upserting {len(records)} records with council_id={cid}")

            BATCH = 20
            saved = 0
            ok = True
            for i in range(0, len(records), BATCH):
                if await _supa_upsert(records[i:i + BATCH]):
                    saved += len(records[i:i + BATCH])
                else:
                    ok = False

            if ok:
                await _supa_patch_council(cid, {
                    "coverage_source": "civica_scraper",
                    "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                    "consecutive_empty_runs": 0,
                    "active": True,
                })
                print(f"    ✓ Saved {saved}")
            else:
                print(f"    ⚠ Partial save: {saved} of {len(apps)}")
                if saved > 0:
                    await _supa_patch_council(cid, {
                        "last_saved_at": datetime.now(timezone.utc).isoformat(),
                        "consecutive_empty_runs": 0,
                    })
            return saved
        except Exception as e:
            print(f"    ✗ Error after finding {len(apps)} application(s) for "
                  f"[{portal.council_name}] (council_id={cid}) — nothing saved: {e}")
            return 0


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Civica scraper (Playwright)")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"Days back:   {DAYS_BACK}")
    print(f"SUPABASE:    {'set' if SUPABASE_URL else 'NOT SET'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    try:
        from civica_councils import CIVICA_COUNCILS, COUNCIL_DB_IDS
    except ImportError:
        print("ERROR: civica_councils.py not found")
        sys.exit(1)

    try:
        db_rows = await _supa_get(
            "councils", select="id,name,last_scraped_at", order="last_scraped_at.asc.nullsfirst", limit="600",
        )
    except Exception as e:
        print(f"Failed to fetch councils: {e}")
        sys.exit(1)

    db_by_name = {r["name"].lower(): r["id"] for r in db_rows}

    to_scrape: list[CivicaPortal] = []
    missing = []
    for name, base_url in CIVICA_COUNCILS:
        council_id = COUNCIL_DB_IDS.get(name) or db_by_name.get(name.lower())
        if not council_id:
            for db_name, db_id in db_by_name.items():
                if name.lower() in db_name or db_name in name.lower():
                    council_id = db_id
                    break
        if council_id:
            id_source = "HARDCODED" if name in COUNCIL_DB_IDS else "db-lookup"
            if id_source == "HARDCODED":
                print(f"  [HARDCODED] {name} → id={council_id}")
            to_scrape.append(CivicaPortal(name, base_url, council_id))
        else:
            missing.append(name)

    if missing:
        print(f"Not in DB (skipping): {', '.join(missing)}\n")

    print(f"Scraping {len(to_scrape)} councils with Playwright…\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        print(f"Chromium launched: {browser.version}\n")

        sem = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(
            *[process_council(p, browser, sem) for p in to_scrape],
            return_exceptions=True,
        )
        await browser.close()

    total = sum(r for r in results if isinstance(r, int))
    time_skipped = sum(1 for r in results if r == "TIME_BUDGET_SKIP")
    zero_result = sum(1 for r in results if r == 0)
    errors = sum(1 for r in results if isinstance(r, Exception))

    print("\n" + "=" * 50)
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")
    if zero_result:
        print(f"Saved 0 (other reason — see per-council log lines above): {zero_result} councils")
    if time_skipped:
        print(f"Skipped (time budget): {time_skipped} councils")
    if errors:
        print(f"Errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
