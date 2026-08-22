#!/usr/bin/env python3
"""
PlanFind Northgate PlanningExplorer scraper — Playwright edition.

Built 2026-07-24 from real recon evidence (northgate_recon.py +
northgate_runnymede_results_recon.py) against Runnymede Borough Council,
the first confirmed-working Northgate PlanningExplorer council. See
northgate_councils.py's module docstring for how confirmed councils get
added.

ARCHITECTURE — genuine ASP.NET WebForms, closer to a classic server-
rendered app than either Arcus or Civica:
  - The search form uses real VIEWSTATE/EVENTVALIDATION postback fields
    (confirmed via real HTML) — Playwright's real form-fill + click
    handles this naturally, the same way a real browser would, no
    special VIEWSTATE handling needed in our own code.
  - CONFIRMED real field IDs: #rbRange (radio, select date-range mode),
    #dateStart / #dateEnd (text inputs, dd/mm/yyyy), #csbtnSearch
    (submit button).
  - Results are a clean, semantic <table class="display_table"> — each
    header <th> has a real column name, and CRITICALLY each data <td>
    has a title attribute EXACTLY matching its column name
    (title="Site Address", title="Status", title="Decision", etc.) —
    genuinely reliable to select by, unlike guessing positional index.
  - GENUINE WIN over Civica: Status and Decision are both real, separate,
    visible columns in the results list itself (e.g. "REGISTERED" / "",
    or "FINAL DECISION" / "Approve") — no detail-page visit needed to
    get accurate status, unlike Civica's v1 which had to default
    everything to "pending".
  - Pagination is real "next page" links, but their href points at a
    server-side temp XML file unique to that specific search (confirmed
    via real evidence) — NOT a predictable/constructible URL pattern.
    Must click through the real "next page" link each time, found via
    its child <img alt="Go to next page "> (note trailing space,
    confirmed in real HTML) — a shared skin/template convention likely
    consistent across other Northgate councils too, not Runnymede-
    specific.
  - council_url is the general search page, NOT a per-application detail
    link — this was tried and reverted (2026-07-25) after real, thorough
    testing: a per-application "StdDetails.aspx" link was built from
    each row's own href, but ALL 28 stored links failed with HTTP 404 on
    a genuine health check (northgate_url_healthcheck.py), including
    ones with a fully correct, cleanly-populated query string — ruling
    out data corruption. A follow-up test established real session
    cookies (ASP.NET_SessionId, MVMSession) first and the SAME URL still
    404'd, ruling out session-dependency too. The likely remaining
    explanation: these detail links are tied to an ephemeral, session-
    specific temporary result set (the same pattern already confirmed
    for pagination links, which reference a session-specific temp XML
    file) — genuinely valid only within the live browsing session that
    generated them, not a stable, storable permalink. Real applications
    still have a real, reliable reference (e.g. "RU.26/0984") users can
    search with directly on the council's own site — just not a direct
    deep-link from ours.

HONEST LIMITATION: only ONE council (Runnymede) is confirmed working.
Birmingham (persistent 503, confirmed twice independently) and Tamworth
(persistent timeout, confirmed twice independently) both failed cleanly
on isolated retries — real, repeated evidence, not yet understood
further. Islington's URL is confirmed DEAD (their own site: "In April
2024, we changed our planning application system"). Any new council
needs the same recon-first discipline as Arcus/Civica before being
added.
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
from playwright.async_api import (
    async_playwright,
    Browser,
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
# Helpers — field normalization / parsing
# ---------------------------------------------------------------------------
_DECISION_DECIDED_DIAGNOSED: set[str] = set()


def _normalise_status(status_raw: str, decision_raw: str, council_name: str) -> str:
    """CONFIRMED real values seen in recon: status 'REGISTERED' (pending)
    or 'FINAL DECISION' (decided); decision 'Approve' when decided.
    Refuse/withdrawn wording not yet directly observed — defensive
    matching with a diagnostic for anything unrecognised, so a wrong
    guess is visible rather than silently mis-categorised."""
    s = (status_raw or "").strip().lower()
    d = (decision_raw or "").strip().lower()

    if not d:
        return "pending"

    # ADDED 2026-08-22 — real, confirmed value from Runnymede: "No
    # objection" didn't match any existing keyword, defaulting
    # incorrectly to pending despite being a genuine, real decided
    # outcome. Same real precedent already established for statmap's
    # East Staffordshire data ("No Objection" -> approved) — a
    # consultation-style "no objection" response is functionally
    # approved-adjacent, not still pending.
    if "no objection" in d:
        return "approved"
    if any(x in d for x in ("approv", "grant", "permit", "allow")):
        return "approved"
    if any(x in d for x in ("refus", "reject", "dismiss")):
        return "refused"
    if "withdraw" in d:
        return "withdrawn"

    if d and council_name not in _DECISION_DECIDED_DIAGNOSED:
        _DECISION_DECIDED_DIAGNOSED.add(council_name)
        print(f"    ⚠ DECISION TEXT DIAGNOSTIC [{council_name}]: status={status_raw!r} "
              f"decision={decision_raw!r} — didn't match any known outcome pattern, "
              f"defaulting to pending. Worth checking real wording.")
    return "pending"


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_results_table(html: str, base_url: str, council_name: str) -> list[dict]:
    """Parses the real, confirmed table structure — a clean <table
    class="display_table"> with <td title="..."> matching each column
    name exactly. Genuinely reliable selectors, not guessed positional
    indices."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="display_table")
    if not table:
        return []

    apps = []
    for tr in table.find_all("tr"):
        # Header rows use <th>, data rows use <td> — skip headers
        if tr.find("th"):
            continue

        ref_cell = tr.find("td", class_="TableData")
        if not ref_cell:
            continue
        ref_link = ref_cell.find("a")
        if not ref_link:
            continue
        reference = ref_link.get_text(strip=True)

        # REVERTED (2026-07-25): this used to construct a per-application
        # detail_url from the row's own href. Confirmed via real,
        # conclusive testing that this doesn't work — ALL 28 stored URLs
        # failed with HTTP 404 (northgate_url_healthcheck.py), including
        # ones with a fully correct, cleanly-populated XMLSIDE value,
        # ruling out data corruption as the cause. A follow-up test
        # established real session cookies first (ASP.NET_SessionId,
        # MVMSession) and the SAME URL still 404'd, ruling out session-
        # dependency too. The most likely remaining explanation: these
        # detail links are tied to an ephemeral, session-specific
        # temporary result set (the SAME pattern already confirmed for
        # pagination links, which reference a session-specific temp XML
        # file) — genuinely valid only within the live browsing session
        # that generated them, not a stable, storable permalink. Rather
        # than keep patching URL parameters against a structural platform
        # limitation, falling back to the general search page — same
        # honest treatment already used for manual_link councils. Users
        # can still search using the real reference we DO reliably
        # capture (e.g. "RU.26/0984"), just not via a direct deep-link.
        detail_url = base_url

        def _cell_text(title: str) -> str:
            cell = tr.find("td", attrs={"title": title})
            if not cell:
                return ""
            # Real addresses contain embedded newlines (confirmed in
            # recon evidence) — normalise to a single space-joined string
            return " ".join(cell.get_text(separator=" ", strip=True).split())

        address = _cell_text("Site Address")
        proposal = _cell_text("Development Description")
        status_raw = _cell_text("Status")
        date_registered = _cell_text("Date Registered")
        decision_raw = _cell_text("Decision")

        apps.append({
            "reference":        reference,
            "address":          address,
            "postcode":         _extract_postcode(address),
            "description":      proposal,
            "application_type": "",
            "status":           _normalise_status(status_raw, decision_raw, council_name),
            "submitted_date":   _parse_date(date_registered),
            "decision_date":    _parse_date(date_registered) if decision_raw.strip() else None,
            "council_url":      detail_url,
            "source":           "northgate_scraper",
        })

    return apps


# ---------------------------------------------------------------------------
# Supabase REST API — identical to idox_scraper.py/arcus_scraper.py/
# civica_scraper.py, same table/schema
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
# Geocoding — identical to idox_scraper.py/arcus_scraper.py/civica_scraper.py
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
# NorthgatePortal
# ---------------------------------------------------------------------------
class NorthgatePortal:
    def __init__(self, council_name: str, base_url: str, db_council_id: int):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.db_council_id = db_council_id

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

        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"    ⚠ [{self.council_name}] Navigation error: {e}")
            await context.close()
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        # ADDED 2026-08-22 — real, confirmed cause of Conwy's "element
        # is not visible" failure on the exact same #rbRange field that
        # works fine for Runnymede: no overlay-dismissal logic existed
        # in this file at all. Real evidence from elsewhere in this
        # project (South Tyneside, Northgate servlet family) confirms
        # different councils on the same underlying platform can have
        # genuinely different cookie-consent implementations — one
        # council's page can have a real blocking overlay the other
        # doesn't. Reusing the same, already-proven dismissal pattern
        # rather than reinventing it.
        for selector in ["#ivcb-overlay button", "#ivcb-overlay .accept",
                          "button:has-text('Accept')", "button:has-text('I agree')",
                          "button:has-text('Close')", "[id*='cookie'] button"]:
            try:
                el = page.locator(selector).first
                if await el.count() > 0 and await el.is_visible(timeout=2000):
                    await el.click(timeout=3000)
                    await asyncio.sleep(1)
                    print(f"    [{self.council_name}] Dismissed a real overlay/cookie "
                          f"banner via {selector!r}")
                    break
            except Exception:
                continue

        # ADDED 2026-08-22 (round 2) — real, decisive diagnostic
        # evidence: the overlay fix above genuinely deployed but was
        # never the real cause. A direct JS inspection confirmed
        # #rbRange sits inside a real ancestor div, id="advancedSearch",
        # with display:none by default — Conwy's page has a genuine
        # Simple/Advanced Search toggle (confirmed visually too: a real
        # "Advanced Search" button sits right next to "Search"), and
        # the advanced fields (including our real #rbRange target)
        # start hidden until that's clicked. Runnymede's own page
        # apparently defaults to this section already open, which is
        # exactly why only Conwy hit this. Checking whether #rbRange is
        # already visible first, so this doesn't change anything for
        # Runnymede's already-working flow — only clicking the real
        # toggle when it's genuinely needed.
        try:
            rbrange_visible = await page.locator("#rbRange").is_visible(timeout=1000)
        except Exception:
            rbrange_visible = False

        if not rbrange_visible:
            try:
                adv_btn = page.get_by_role("button", name="Advanced Search", exact=False)
                if await adv_btn.count() > 0:
                    await adv_btn.first.click(timeout=5_000)
                    await asyncio.sleep(1)
                    print(f"    [{self.council_name}] #rbRange wasn't visible — "
                          f"clicked the real 'Advanced Search' toggle to reveal it")
            except Exception as e:
                print(f"    ⚠ [{self.council_name}] Could not click 'Advanced "
                      f"Search' toggle: {e}")

        # CONFIRMED real field IDs (Runnymede) — genuinely working form-
        # fill + submit, ASP.NET postback handled naturally by Playwright
        try:
            await page.check("#rbRange", timeout=5_000)
            await page.fill("#dateStart", date_from.strftime("%d/%m/%Y"), timeout=5_000)
            await page.fill("#dateEnd", today.strftime("%d/%m/%Y"), timeout=5_000)
            await page.click("#csbtnSearch", timeout=5_000)
        except Exception as e:
            print(f"    ⚠ [{self.council_name}] Form interaction failed: {e}")
            await context.close()
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(2)

        all_apps: list[dict] = []
        seen_refs: set[str] = set()

        for page_num in range(1, MAX_PAGES + 1):
            html = await page.content()

            if "display_table" not in html:
                # DIAGNOSTIC (2026-07-24): zero results is either genuine
                # (nothing in this date range) or a real problem (server
                # error, wrong field IDs for this specific council).
                # Print real evidence, same principle as every other
                # diagnostic this session.
                title = await page.title()
                body_snippet = ""
                try:
                    body_text = await page.locator("body").inner_text()
                    body_snippet = " ".join(body_text.split())[:300]
                except Exception:
                    pass
                print(f"    ⚠ [{self.council_name}] No results table found "
                      f"(page {page_num}) — title: {title!r}, body: {body_snippet!r}")
                break

            page_apps = _parse_results_table(html, self.base_url, self.council_name)
            new_count = 0
            for app in page_apps:
                if app["reference"] not in seen_refs:
                    seen_refs.add(app["reference"])
                    all_apps.append(app)
                    new_count += 1

            if new_count == 0:
                break

            # Real "next page" link, confirmed via its child <img
            # alt="Go to next page "> (note trailing space, confirmed in
            # real HTML) — a shared skin/template convention, not
            # Runnymede-specific. href points at a server-side temp file
            # unique to this search, so must click the real link found
            # on THIS page, not construct one.
            next_link = page.locator("a:has(img[alt='Go to next page '])")
            if await next_link.count() == 0:
                break
            try:
                await next_link.first.click(timeout=5_000)
                await asyncio.sleep(1)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1)
            except Exception:
                break

        await context.close()
        return all_apps


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def process_council(portal: NorthgatePortal, browser: Browser, sem: asyncio.Semaphore) -> int:
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
                "source":           "northgate_scraper",
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
                    "coverage_source": "northgate_scraper",
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Northgate scraper (Playwright)")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"Days back:   {DAYS_BACK}")
    print(f"SUPABASE:    {'set' if SUPABASE_URL else 'NOT SET'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    try:
        from northgate_councils import NORTHGATE_COUNCILS, COUNCIL_DB_IDS
    except ImportError:
        print("ERROR: northgate_councils.py not found")
        sys.exit(1)

    try:
        db_rows = await _supa_get(
            "councils", select="id,name,last_scraped_at", order="last_scraped_at.asc.nullsfirst", limit="600",
        )
    except Exception as e:
        print(f"Failed to fetch councils: {e}")
        sys.exit(1)

    db_by_name = {r["name"].lower(): r["id"] for r in db_rows}

    to_scrape: list[NorthgatePortal] = []
    missing = []
    for name, base_url in NORTHGATE_COUNCILS:
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
            to_scrape.append(NorthgatePortal(name, base_url, council_id))
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
