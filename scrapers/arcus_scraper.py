#!/usr/bin/env python3
"""
PlanFind Arcus scraper — Playwright edition.

Scrapes Arcus (Salesforce Built Environment) planning portals. Built from
12 rounds of live reconnaissance (2026-07-13) against 3 confirmed working
councils on different domain shapes (my.site.com subdomains AND a custom
domain with a broken SSL cert). See arcus_councils.py's module docstring
for the full mechanism writeup.

Architecture, deliberately mirroring idox_scraper.py's structure and
helper-function names/patterns so both scrapers are easy to navigate
side by side:
  - One shared browser instance, one isolated BrowserContext per council
  - Semaphore limits concurrent councils (kept lower than Idox's — each
    Arcus page load is a genuine JS-heavy Salesforce Lightning render,
    meaningfully heavier per-page than Idox's plain server-rendered HTML)
  - Navigate → click confirmed "Weekly lists" link → try CSV download →
    fall back to HTML text-parsing if CSV isn't offered
  - Same Supabase upsert / geocoding / council-health-tracking pipeline as
    idox_scraper.py — same table, same schema, same coverage_source
    concept (just 'arcus_scraper' instead of 'idox_scraper')

KEY DIFFERENCES FROM IDOX, worth remembering:
  - No universal URL parameter for date ranges — Idox's monthYearIndex has
    no Arcus equivalent that survives across councils (round 3 proved the
    raw c__q URL parameter does NOT transfer between councils).
  - Each council needs its own confirmed "Weekly lists" link text —
    guessing does not work (3 different councils, 3 different exact
    wordings). New councils MUST be reconnoitred with arcus_recon.py and a
    real screenshot before being added to ARCUS_COUNCILS.
  - CSV export works reliably on weekly-list views but is NOT guaranteed —
    round 12 showed it failing for 2 of 3 councils even after real results
    rendered correctly. The HTML fallback parser is a genuine, load-bearing
    part of this scraper, not a rare edge case.
  - No reliable per-application deep-link URL is available from CSV
    exports (unlike Idox, which always has a direct applicationDetails.do
    URL). council_url falls back to the council's own portal homepage.
"""
import asyncio
import csv
import io
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
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
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "2"))  # lower than Idox —
                                                          # each page is a
                                                          # heavier JS render

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 3


# ---------------------------------------------------------------------------
# Helpers — field normalization (Arcus-specific formats seen in recon)
# ---------------------------------------------------------------------------
def _normalise_status(status_raw: str, decision_raw: str = "") -> str:
    """Arcus status/decision values seen in recon: 'Under Consultation',
    'Valid', 'Decision Made', 'Final' for status; 'Approve with
    Conditions' for decision. The decision column, when present, is a
    stronger signal than status alone for approved/refused.
    """
    s = (status_raw or "").lower()
    d = (decision_raw or "").lower()

    if d:
        if any(x in d for x in ("approv", "grant", "permit", "allow")):
            return "approved"
        if any(x in d for x in ("refus", "reject", "dismiss")):
            return "refused"

    if any(x in s for x in ("decision made", "final", "approved", "granted")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
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
    for sep in ("+", "T", " "):
        if sep in s:
            s = s.split(sep)[0].strip()
    s = s[:10]
    for fmt in (
        "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y",
        "%d/%m/%y", "%Y/%m/%d",
        "%d %B %Y", "%d %b %Y",
    ):
        try:
            # Python's strptime handles non-zero-padded values fine (e.g.
            # "6/7/2026" or "09/7/2026", both seen in real Arcus CSV
            # exports) — no special-casing needed beyond Idox's own list.
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Supabase REST API — identical to idox_scraper.py, same table/schema
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
    """Same council-health tracking as idox_scraper.py — atomic RPC
    increment, see migration_council_health.sql. Reused here so Arcus
    councils get the same silent-breakage monitoring from day one, rather
    than needing a separate follow-up session to add it retroactively.
    """
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
# Geocoding — identical to idox_scraper.py
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
                # VISIBILITY FIX (2026-07-21): this used to be a bare
                # `except Exception: pass` — a genuinely silent failure
                # point with zero trace. Found while investigating a real
                # case (Eastleigh Borough Council, 2026-07-21) where a
                # council's own log output stopped cleanly right after
                # "Geocoding N postcodes…" with no upsert line, no save,
                # no error, and its consecutive_empty_runs counter frozen
                # (neither reset by success nor incremented by the
                # genuine-empty path) — meaning whatever happened here
                # left no evidence at all. This doesn't change behavior
                # (still continues to the next batch either way), it just
                # means a future occurrence gets logged instead of
                # vanishing without a trace.
                print(f"    ⚠ Geocoding batch failed ({len(unique[i:i + 100])} postcodes): {e}")
            await asyncio.sleep(0.3)
    return results


# ---------------------------------------------------------------------------
# CSV parsing — the PRIMARY data path when available
# ---------------------------------------------------------------------------
def _parse_csv(csv_text: str, council_name: str) -> list[dict]:
    """Arcus CSV column names seen so far ONLY confirmed for Ashford:
    "Application Reference","Site Address","Proposal","Date Valid",
    "Status","Decision". Other councils' CSVs haven't been captured yet —
    this uses a flexible, case-insensitive header lookup with several
    fallback names per field, same defensive pattern as Idox's
    _parse_result field lookup chain, since we should expect column
    naming to vary by council just like everything else about Arcus has.
    """
    apps = []
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
    except Exception as e:
        print(f"    ⚠ CSV parse error: {e}")
        return apps

    for row in reader:
        if not row:
            continue
        row_lower = {
            (k or "").strip().lower(): (v or "").strip()
            for k, v in row.items()
        }

        ref = (
            row_lower.get("application reference") or
            row_lower.get("reference") or
            row_lower.get("ref") or
            ""
        ).strip()
        if not ref or len(ref) < 3:
            continue

        address = (
            row_lower.get("site address") or
            row_lower.get("address") or
            ""
        ).strip()

        proposal = (
            row_lower.get("proposal") or
            row_lower.get("description") or
            row_lower.get("description of works") or
            ""
        ).strip()

        date_raw = (
            row_lower.get("date valid") or
            row_lower.get("valid date") or
            row_lower.get("received date") or
            row_lower.get("date received") or
            ""
        )

        app_type = (
            row_lower.get("application type") or
            row_lower.get("record type") or
            ""
        ).strip()

        status_raw = row_lower.get("status") or row_lower.get("application status") or ""
        decision_raw = row_lower.get("decision") or ""
        status = _normalise_status(status_raw, decision_raw)

        # Column name NOT yet confirmed via a real captured CSV sample
        # (the docstring above only confirms Ashford's non-decision-date
        # columns) — candidates listed defensively, same pattern as every
        # other field in this function, harmless if the column doesn't
        # exist (.get() just returns empty).
        decision_date_raw = (
            row_lower.get("decision notice sent date") or
            row_lower.get("decision notice sent") or
            row_lower.get("decision date") or
            row_lower.get("decided date") or
            ""
        )
        decision_date = _parse_date(decision_date_raw)

        if status in ("approved", "refused") and not decision_date and council_name not in _DECISION_DATE_DIAGNOSED:
            _DECISION_DATE_DIAGNOSED.add(council_name)
            print(f"    ⚠ DECISION DATE DIAGNOSTIC [{council_name}] (CSV): status is "
                  f"'{status}' but no decision date matched any known column. "
                  f"Columns seen: {list(row_lower.keys())}")

        apps.append({
            "reference":        ref,
            "address":          address,
            "postcode":         _extract_postcode(address),
            "description":      proposal,
            "application_type": app_type,
            "status":           status,
            "submitted_date":   _parse_date(date_raw),
            "decision_date":    decision_date,
            "council_name":     council_name,
            "council_url":      None,  # filled in by caller with base_url fallback
            "source":           "arcus_scraper",
        })

    return apps


# ---------------------------------------------------------------------------
# HTML text-based fallback parser — used when CSV isn't offered
# ---------------------------------------------------------------------------
# Results render as Lightning custom components, NOT plain <table>/<tr>/<td>
# elements (confirmed via diagnostic tag counts in recon round 1 — those
# counts stayed at 0 even after real content rendered). Operating on the
# flattened VISIBLE TEXT is more robust to unknown/varying component
# nesting than trying to target specific tag names.
# 2026-07-21: tracks councils where a genuinely decided application had
# no decision_date matched by any known label — same rate-limited
# diagnostic pattern as idox_scraper.py's equivalent. Unlike Idox's case
# (where the official decision date was confirmed genuinely absent from
# the source data), Arcus's data model does carry a real decision date
# (confirmed via Wiltshire screenshot evidence) — a miss here more likely
# means an unrecognised label wording for a given council, worth
# investigating with real evidence rather than assumed benign.
_DECISION_DATE_DIAGNOSED: set[str] = set()

_LABEL_PATTERNS = {
    "reference": r"Application Reference|Reference",
    "address":   r"Site Address|Site address|Address",
    "proposal":  r"Proposal|Description(?:\s+of\s+works)?",
    "date":      r"Date Valid|Valid Date|Received Date|Date Received",
    "type":      r"Application [Tt]ype|Record [Tt]ype",
    "status":    r"Application Status|Status",
    "decision":  r"Decision",
    # Added 2026-07-21, confirmed via real Wiltshire screenshot evidence
    # (detail page label "Decision Notice Sent Date", real value populated
    # for a genuinely closed application: 21/07/2026) — this ISN'T the
    # same gap Idox had (where the council's official decision date
    # genuinely doesn't exist in the source data at all); Arcus's data
    # model does carry a real decision date, our parser just never looked
    # for it. Listed separately from "date" above (which matches the
    # SUBMITTED date) so the two never collide in _field_for_label.
    "decision_date": r"Decision Notice Sent(?:\s+Date)?|Decision Date|Decided Date",
}
_ALL_LABELS_RE = re.compile(
    r"^(" + "|".join(_LABEL_PATTERNS.values()) + r")$"
)


def _field_for_label(label_text: str) -> Optional[str]:
    for field, pattern in _LABEL_PATTERNS.items():
        if re.match(rf"^(?:{pattern})$", label_text):
            return field
    return None


def _parse_results_html_fallback(html: str, council_name: str) -> list[dict]:
    """BEST-EFFORT fallback for councils where CSV export isn't offered.
    This is a genuinely weaker code path than CSV parsing — flagged
    honestly rather than pretending it's equally reliable. Works by
    walking the page's flattened text looking for the repeating
    label/value block pattern seen consistently across every council's
    results view in recon screenshots (e.g. "Application Reference" /
    "PA/2026/1127" / "Site Address" / "44 Homestead..." / ...), starting
    a new record every time a "reference" label is seen.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [l for l in text.split("\n") if l.strip()]

    records: list[dict] = []
    current: dict = {}
    i = 0
    while i < len(lines):
        m = _ALL_LABELS_RE.match(lines[i])
        if m and i + 1 < len(lines):
            field = _field_for_label(m.group(1))
            value = lines[i + 1]
            if field == "reference":
                if current.get("reference"):
                    records.append(current)
                current = {"reference": value}
            elif field:
                current[field] = value
            i += 2
        else:
            i += 1
    if current.get("reference"):
        records.append(current)

    apps = []
    for r in records:
        ref = r.get("reference", "").strip()
        # Guard against accidentally capturing a non-reference value (e.g.
        # a stray heading) — real Arcus references seen so far always
        # contain a digit and either a slash or hyphen.
        if not ref or len(ref) < 3 or not re.search(r"\d", ref) or not re.search(r"[/-]", ref):
            continue
        address = r.get("address", "")
        status = _normalise_status(r.get("status", ""), r.get("decision", ""))
        decision_date = _parse_date(r.get("decision_date", ""))

        # DECISION DATE DIAGNOSTIC (2026-07-21) — see _DECISION_DATE_DIAGNOSED
        # comment above for why this differs from Idox's equivalent.
        if status in ("approved", "refused") and not decision_date and council_name not in _DECISION_DATE_DIAGNOSED:
            _DECISION_DATE_DIAGNOSED.add(council_name)
            print(f"    ⚠ DECISION DATE DIAGNOSTIC [{council_name}]: status is "
                  f"'{status}' but no decision date matched any known label. "
                  f"Full fields: {r}")

        apps.append({
            "reference":        ref,
            "address":          address,
            "postcode":         _extract_postcode(address),
            "description":      r.get("proposal", ""),
            "application_type": r.get("type", ""),
            "status":           status,
            "submitted_date":   _parse_date(r.get("date", "")),
            "decision_date":    decision_date,
            "council_name":     council_name,
            "council_url":      None,
            "source":           "arcus_scraper",
        })
    return apps


# ---------------------------------------------------------------------------
# Playwright scraper
# ---------------------------------------------------------------------------
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport":       {"width": 1280, "height": 900},
    "locale":         "en-GB",
    "timezone_id":    "Europe/London",
    "java_script_enabled": True,
    "ignore_https_errors": True,  # REQUIRED — Manchester's custom domain
                                    # has a certificate Chromium rejects by
                                    # default (confirmed root cause of
                                    # early recon rounds silently failing).
    "accept_downloads": True,
}


def _compute_recheck_date_from(pending_recheck: list[dict], today: date,
                                normal_days_back: int = 14,
                                floor_days_back: int = 120) -> date:
    """Given a pending-recheck list, returns the date-from value
    _scrape_advanced_search should use: the normal 14-day window, unless
    a pending application's submitted_date is older than that AND within
    the 120-day floor — in which case widen back to cover it. Pulled out
    as a standalone function (not inline in the Playwright method) so
    this logic can be tested directly without a browser."""
    normal_cutoff = today - timedelta(days=normal_days_back)
    floor_date = today - timedelta(days=floor_days_back)

    earliest_recheck = None
    for p in pending_recheck:
        d = p.get("submitted_date")
        if not d:
            continue
        try:
            parsed = date.fromisoformat(d)
        except ValueError:
            continue
        if earliest_recheck is None or parsed < earliest_recheck:
            earliest_recheck = parsed

    if earliest_recheck is None:
        return normal_cutoff
    return min(normal_cutoff, max(earliest_recheck, floor_date))


class ArcusPortal:
    """Scrapes one Arcus planning portal via Playwright.

    Supports three distinct navigation patterns, discovered across two
    days of reconnaissance since not every council's Arcus instance uses
    the same homepage layout:

      mode="weekly_list" (most councils): homepage has a "Weekly lists" or
        "Quick links" section with one or more clickable links leading
        straight to results. config = list of exact link texts to click.

      mode="advanced_search" (Bromley, Bracknell Forest, Milton Keynes):
        homepage has NO quick-link shortcut at all — only "Advanced
        search". Uses the exact sequence proven in recon round 12: click
        Advanced search, select "Planning Applications" category via
        Lightning combobox, fill a 14-day date range, click the LAST
        "Search" button on the page (critical fix from round 12 — there
        are usually 2-3 buttons named "Search", the form's real submit
        button is reliably the last one in document order). config unused.

      mode="tabbed_weekly_list" (Eastleigh, Isle of Anglesey): a visibly
        different Arcus template using tabbed navigation (Quick Search /
        Advanced Search / Weekly List as tabs) rather than homepage links.
        Click the "Weekly List" tab; some councils (Eastleigh) render
        results immediately with no further interaction, others (Anglesey)
        need a category dropdown selected first. config = the exact
        dropdown option text to select if one is needed, or None if
        results render directly off the tab click alone.

    NOTE: advanced_search and tabbed_weekly_list modes are built directly
    from documented recon findings but have NOT yet been run against a
    live council in production — unlike weekly_list mode, which has run
    successfully for 7 councils across 3 nights. Treat the first live run
    using either new mode as the real test, the same way weekly_list mode
    itself was validated.
    """

    def __init__(self, council_name: str, base_url: str, mode: str,
                 config, db_council_id: int,
                 pending_recheck: Optional[list[dict]] = None,
                 register_url_override: Optional[str] = None):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.config = config
        self.db_council_id = db_council_id
        # OVERRIDE FIX (2026-07-21): found via real evidence — Wiltshire
        # Council's Salesforce site returns a genuine "Invalid Page"
        # error for the standard "/register-view?c__r=Arcus_BE_Public_
        # Register" suffix that every OTHER Arcus council in this file
        # resolves correctly. A manual browser test confirmed the BARE
        # base_url (no suffix at all) loads a real, working homepage with
        # all three tabs visible. Rather than guess a different universal
        # suffix (risking breaking the 8 councils where the standard one
        # already works), this is a per-council override — only Wiltshire
        # passes one, everyone else keeps the proven default.
        self.register_url = register_url_override or (
            f"{self.base_url}/register-view?c__r=Arcus_BE_Public_Register"
        )
        # DECISION-CADENCE FIX (2026-07-21): only usable by
        # _scrape_advanced_search — that's the only one of Arcus's three
        # modes with an actual parameterized date range (weekly_list and
        # tabbed_weekly_list just click a fixed "last 7 days" link with
        # no date field to widen at all). For the 3 councils using
        # advanced_search (Bromley, Bracknell Forest, Milton Keynes),
        # this lets the date-from field reach back far enough to
        # re-observe already-tracked pending applications outside the
        # normal 14-day window, so a genuine transition to
        # approved/refused can actually be caught by the
        # decision_detected_at trigger — same problem, same fix shape as
        # idox_scraper.py's pending_recheck, just via an explicit date
        # field instead of a month index. The other 9 councils (weekly_list/
        # tabbed_weekly_list modes) get NO benefit from this — their
        # decision-cadence gap remains open until/unless a genuinely
        # different site-interaction mechanism is found for them.
        self.pending_recheck = pending_recheck or []

    async def scrape(self, browser: Browser) -> list[dict]:
        all_apps: list[dict] = []
        context: BrowserContext = await browser.new_context(**CONTEXT_OPTIONS)
        try:
            page: Page = await context.new_page()

            if self.mode == "weekly_list":
                for link_text in self.config:
                    apps = await self._scrape_weekly_list(page, link_text)
                    all_apps.extend(apps)

            elif self.mode == "advanced_search":
                apps = await self._scrape_advanced_search(page)
                all_apps.extend(apps)

            elif self.mode == "tabbed_weekly_list":
                # MULTI-CATEGORY FIX (2026-07-21): originally built only
                # for Eastleigh/Anglesey, which each need at most ONE
                # category (or none at all). Wiltshire's tabbed template
                # has TWO real weekly categories ("...Validated this
                # week" / "...Decided this week" — confirmed via
                # screenshot, same wording Epping Forest already uses in
                # weekly_list mode). Rather than restructure
                # _scrape_tabbed_weekly_list itself, config can now be a
                # list of category hints — loop with a fresh navigation
                # per category, exactly matching how weekly_list mode
                # already loops over multiple link texts in this same
                # dispatch block below. A single string or None still
                # works exactly as before for Eastleigh/Anglesey.
                if isinstance(self.config, list):
                    for category_hint in self.config:
                        apps = await self._scrape_tabbed_weekly_list(page, category_hint)
                        all_apps.extend(apps)
                else:
                    apps = await self._scrape_tabbed_weekly_list(page, self.config)
                    all_apps.extend(apps)

            else:
                print(f"    ✗ Unknown mode '{self.mode}' — skipping")

        except Exception as e:
            print(f"    ✗ Context error: {e}")
        finally:
            await context.close()

        # Fill in the council_url fallback here, once, rather than in every
        # parser branch — the council's own portal homepage, since no
        # reliable per-application deep link survives a CSV export.
        for app in all_apps:
            if not app.get("council_url"):
                app["council_url"] = self.register_url

        return all_apps

    async def _scrape_weekly_list(self, page: Page, link_text: str) -> list[dict]:
        # --- Step 1: navigate fresh for each configured link (safer than
        # reusing page state across multiple list types on the same
        # council — avoids any risk of stale form state bleeding between
        # the two lists Epping Forest/Manchester each have). ---
        try:
            await page.goto(self.register_url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
        except PlaywrightTimeout:
            print(f"    ⚠ Page load timeout for '{link_text}'")
            return []
        except Exception as e:
            print(f"    ⚠ Navigation error: {e}")
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass  # Common for Lightning apps — not necessarily a problem
        await asyncio.sleep(5)  # Lightning needs real time to finish
                                  # rendering after domcontentloaded/networkidle

        # --- Step 2: click the confirmed link text for this council ---
        try:
            loc = page.get_by_text(link_text, exact=False)
            if await loc.count() == 0:
                print(f"    ⚠ Link text not found: '{link_text}' "
                      f"(council may have changed its wording — re-run "
                      f"arcus_recon.py to confirm)")
                return []
            await loc.first.click(timeout=5_000)
        except Exception as e:
            print(f"    ⚠ Failed to click '{link_text}': {e}")
            return []

        await asyncio.sleep(5)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        # --- Step 3: try CSV first (the reliable, clean path) ---
        apps = await self._try_csv_download(page)
        if apps:
            print(f"    ✓ [{self.council_name}] '{link_text}': {len(apps)} via CSV")
            return apps

        # --- Step 4: fall back to HTML text parsing ---
        html = await page.content()
        apps = _parse_results_html_fallback(html, self.council_name)
        if apps:
            print(f"    ✓ [{self.council_name}] '{link_text}': {len(apps)} via HTML fallback (CSV unavailable)")
        else:
            print(f"    ⚠ [{self.council_name}] '{link_text}': no results found via CSV or HTML fallback")
        return apps

    async def _scrape_advanced_search(self, page: Page) -> list[dict]:
        """For councils with NO homepage weekly-list quick-link (Bromley,
        Bracknell Forest, Milton Keynes) — the exact sequence proven
        working in recon round 12, after 11 earlier rounds of trial and
        error. The critical, non-obvious fix: there are usually 2-3
        buttons named "Search" on the page (a quick-search box near the
        top, plus the Advanced Search form's own submit button) — the
        form's real submit button is reliably the LAST one in document
        order, not the first. Clicking .first here silently submits the
        wrong search and returns nothing, which is exactly what happened
        across rounds 8-11 before this was diagnosed.
        """
        try:
            await page.goto(self.register_url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"    ⚠ Navigation error: {e}")
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(5)

        # --- Click "Advanced search" ---
        try:
            loc = page.get_by_text("Advanced search", exact=False)
            if await loc.count() == 0:
                print(f"    ⚠ 'Advanced search' link not found — council may have "
                      f"changed its homepage, re-run arcus_recon.py to confirm")
                return []
            await loc.first.click(timeout=5_000)
        except Exception as e:
            print(f"    ⚠ Failed to click 'Advanced search': {e}")
            return []

        await asyncio.sleep(5)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        # --- Select Category via Lightning combobox (click to open, click
        # the matching option — NOT select_option(), which only works on
        # native <select> elements and silently fails on Lightning's
        # custom combobox component) ---
        try:
            category_field = page.get_by_label("Category", exact=False)
            if await category_field.count() > 0:
                await category_field.first.click(timeout=5_000)
                await asyncio.sleep(1)
                for variant in ["Planning Applications", "Planning Application", "Planning"]:
                    option = page.get_by_role("option", name=variant, exact=False)
                    if await option.count() > 0:
                        await option.first.click(timeout=5_000)
                        break
                # Selecting the category commonly re-renders the form with a
                # different field set for that application type — give it a
                # proper wait before hunting for date fields (round 11 fix;
                # skipping this wait caused date-field lookups to fail
                # against DOM elements that were mid-swap).
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(3)
        except Exception:
            print("    ⚠ Category selection not confirmed — continuing anyway, "
                  "some councils' Advanced Search may not require it")

        # --- Fill the date range: normally 14 days back, widened to
        # reach the oldest pending-recheck date when present (bounded to
        # 120 days back, matching Idox's equivalent window) — computed
        # by the standalone _compute_recheck_date_from() so this exact
        # logic is directly testable without a browser. ---
        today = date.today()
        date_from = _compute_recheck_date_from(self.pending_recheck, today)
        for label in ["Valid date from", "Date Valid From", "Date from", "Received date from"]:
            try:
                field = page.get_by_label(label, exact=False)
                if await field.count() > 0:
                    await field.first.fill(date_from.strftime("%d/%m/%Y"), timeout=5_000)
                    await field.first.press("Tab")
                    break
            except Exception:
                continue
        for label in ["Valid date to", "Date Valid To", "Date to", "Received date to"]:
            try:
                field = page.get_by_label(label, exact=False)
                if await field.count() > 0:
                    await field.first.fill(today.strftime("%d/%m/%Y"), timeout=5_000)
                    await field.first.press("Tab")
                    break
            except Exception:
                continue

        await asyncio.sleep(1)

        # --- Click the LAST "Search" button — the round-12 fix ---
        try:
            search_buttons = page.get_by_role("button", name="Search", exact=False)
            btn_count = await search_buttons.count()
            if btn_count == 0:
                print("    ⚠ No 'Search' button found")
                return []
            await search_buttons.last.click(timeout=5_000)
        except Exception as e:
            print(f"    ⚠ Error clicking Search: {e}")
            return []

        await asyncio.sleep(5)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        # --- Try CSV first, HTML fallback second — same as weekly_list mode ---
        apps = await self._try_csv_download(page)
        if apps:
            print(f"    ✓ [{self.council_name}] Advanced Search: {len(apps)} via CSV")
            return apps

        html = await page.content()
        apps = _parse_results_html_fallback(html, self.council_name)
        if apps:
            print(f"    ✓ [{self.council_name}] Advanced Search: {len(apps)} via HTML fallback (CSV unavailable)")
        else:
            print(f"    ⚠ [{self.council_name}] Advanced Search: no results found via CSV or HTML fallback")
        return apps

    async def _scrape_tabbed_weekly_list(self, page: Page, category_hint: Optional[str]) -> list[dict]:
        """For councils using the visibly different tabbed Arcus template
        (Eastleigh, Isle of Anglesey) — Quick Search / Advanced Search /
        Weekly List as TABS rather than homepage quick-links. Click the
        "Weekly List" tab; some councils (Eastleigh, confirmed via
        screenshot) render results immediately with no further
        interaction needed. Others (Anglesey) require selecting a
        category from a dropdown first — category_hint is the exact
        dropdown option text to select in that case, or None to skip
        straight to checking for results after the tab click alone.

        BUG FIX (2026-07-16, after first live run): this used to navigate
        to self.register_url (the "/register-view?c__r=Arcus_BE_Public_
        Register" suffix every OTHER Arcus template needs), which caused
        both Eastleigh and Anglesey to fail with "Weekly List tab not
        found" on their live debut. Real screenshots show neither council
        ever uses that suffix — Eastleigh's actual page is
        planning.eastleigh.gov.uk/s/public-register, Anglesey's is
        ioacc.my.site.com/s/pr-english?language=en_GB. Both are just the
        plain base_url with no extra suffix. Navigate there directly.

        BUG FIX #2 (2026-07-16, after second live run): with the URL fix
        above, Anglesey's real tab element was correctly LOCATED this
        time, but the click failed — a cookie consent banner ("cc-panel")
        was sitting on top of it, intercepting the click. Dismiss it
        proactively, and use force=True as a robust fallback regardless
        of which cookie tool a given council's site runs.

        BUG FIX #3 (2026-07-16, same run): the detailed error log also
        revealed Anglesey's category dropdown is a genuine native HTML
        <select> element (confirmed: "<select id=... class='slds-select'
        name='applicationCategory'>"), NOT a Lightning custom combobox
        like the Advanced Search councils use. Native <select> elements
        need Playwright's select_option(), not the click-to-open,
        click-the-option pattern that works for Lightning comboboxes —
        that pattern was failing here because .click() on a native select
        doesn't reveal role="option" elements the way a Lightning
        combobox does. Try select_option() first, fall back to the
        Lightning pattern in case some OTHER tabbed-template council uses
        a genuine combobox instead.
        """
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"    ⚠ Navigation error: {e}")
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(5)

        # --- Best-effort cookie banner dismissal — several common UK
        # council cookie-tool button texts. Silently continues if none
        # match; force=True on the tab click below is the real safety net
        # regardless of whether this succeeds. ---
        for banner_text in ["Accept all", "Accept All", "Accept", "I agree", "Allow all cookies", "Reject"]:
            try:
                banner_btn = page.get_by_role("button", name=banner_text, exact=False)
                if await banner_btn.count() > 0:
                    await banner_btn.first.click(timeout=3_000)
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

        # --- Click the "Weekly List" tab ---
        try:
            tab = page.get_by_text("Weekly List", exact=False)
            if await tab.count() == 0:
                print("    ⚠ 'Weekly List' tab not found — council may have "
                      "changed its layout, re-run arcus_recon.py to confirm")
                return []
            # force=True bypasses Playwright's "receives pointer events"
            # actionability check — safe here specifically because the
            # error log confirmed the element itself is visible, enabled
            # and stable, just visually covered by a cookie banner.
            await tab.first.click(timeout=5_000, force=True)
        except Exception as e:
            print(f"    ⚠ Failed to click 'Weekly List' tab: {e}")
            return []

        await asyncio.sleep(3)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        # --- If a category dropdown needs a specific option selected
        # (Anglesey-style), do that and click Search. If category_hint is
        # None (Eastleigh-style), skip straight to checking for results —
        # confirmed via screenshot that results render directly off the
        # tab click alone for that council. ---
        if category_hint:
            try:
                category_field = page.get_by_label("Application Category", exact=False)
                if await category_field.count() == 0:
                    category_field = page.get_by_label("Category", exact=False)
                if await category_field.count() > 0:
                    selected = False
                    # Try native <select> first — confirmed this is what
                    # Anglesey actually uses.
                    try:
                        await category_field.first.select_option(label=category_hint, timeout=5_000)
                        selected = True
                    except Exception:
                        pass
                    # Fall back to the Lightning combobox pattern in case
                    # some other tabbed-template council uses a genuine
                    # combobox instead of a native select.
                    if not selected:
                        try:
                            await category_field.first.click(timeout=5_000, force=True)
                            await asyncio.sleep(1)
                            option = page.get_by_role("option", name=category_hint, exact=False)
                            if await option.count() > 0:
                                await option.first.click(timeout=5_000, force=True)
                        except Exception:
                            pass
                    await asyncio.sleep(1)

                search_buttons = page.get_by_role("button", name="Search", exact=False)
                if await search_buttons.count() > 0:
                    await search_buttons.last.click(timeout=5_000, force=True)
                    await asyncio.sleep(5)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
            except Exception as e:
                print(f"    ⚠ Category/Search interaction failed: {e}")

        # --- Try CSV first, HTML fallback second ---
        apps = await self._try_csv_download(page)
        if apps:
            print(f"    ✓ [{self.council_name}] Weekly List tab: {len(apps)} via CSV")
            return apps

        html = await page.content()
        apps = _parse_results_html_fallback(html, self.council_name)
        if apps:
            print(f"    ✓ [{self.council_name}] Weekly List tab: {len(apps)} via HTML fallback (CSV unavailable)")
        else:
            print(f"    ⚠ [{self.council_name}] Weekly List tab: no results found via CSV or HTML fallback")
        return apps

    async def _try_csv_download(self, page: Page) -> list[dict]:
        try:
            async with page.expect_download(timeout=10_000) as download_info:
                await page.get_by_text("Download as CSV", exact=False).first.click(timeout=5_000)
            download = await download_info.value
            download_path = await download.path()
            if not download_path:
                return []
            with open(download_path, encoding="utf-8", errors="replace") as f:
                csv_text = f.read()
            return _parse_csv(csv_text, self.council_name)
        except PlaywrightTimeout:
            # CSV genuinely not offered on this view for this council —
            # confirmed to happen (round 12: 2 of 3 councils). Not an
            # error worth logging loudly, the HTML fallback handles it.
            return []
        except Exception as e:
            # VISIBILITY FIX (2026-07-21): this used to be a bare
            # `except Exception: pass`, treating EVERY failure as the
            # same expected "button not there" case above — including
            # genuinely different failures (a corrupted download, a
            # network drop mid-transfer, a real Playwright error) that
            # deserve to be visible rather than silently identical to
            # the known-benign case. Only the specific, already-confirmed
            # timeout case stays silent; anything else now prints.
            print(f"    ⚠ [{self.council_name}] CSV download failed unexpectedly "
                  f"(not the usual 'button not present' case): {e}")
            return []


# ---------------------------------------------------------------------------
# Per-council orchestration — deliberately structured to match
# idox_scraper.py's process_council() closely
# ---------------------------------------------------------------------------
async def process_council(
    portal: ArcusPortal,
    browser: Browser,
    sem: asyncio.Semaphore,
    budget_minutes: int = MAX_MINUTES,
) -> int | str:
    cid = portal.db_council_id

    async with sem:
        # TIME-BUDGET FIX (2026-07-20): this check used to live ONLY in
        # main()'s pre-loop, in a synchronous for-loop that just builds
        # process_council(...) coroutine objects without awaiting them —
        # that happens in microseconds, before any real async time has
        # passed, so elapsed_minutes() was always ~0 there regardless of
        # how long the run had actually been going. This is the exact
        # same root cause idox_scraper.py had before its own Round 4 fix
        # (see that file's history) — Arcus never received the
        # equivalent fix. Checking here, fresh, at the moment this
        # council's turn genuinely arrives (after acquiring the
        # semaphore) reflects real elapsed async time correctly.
        if elapsed_minutes() >= budget_minutes - 3:
            print(f"\n[{portal.council_name}] — skipping, time budget reached "
                  f"({elapsed_minutes():.1f} min elapsed)")
            # Distinct sentinel, not 0 — so main()'s summary can tell a
            # genuine time-skip apart from every other zero-result cause
            # (empty results, errors), instead of conflating them under
            # one misleading "Skipped (time)" label the way this file
            # used to (same fix as idox_scraper.py's equivalent).
            return "TIME_BUDGET_SKIP"

        print(f"\n[{portal.council_name}] (council_id={cid})")
        await asyncio.sleep(1)  # stagger requests

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

        # SAFETY NET (2026-07-21): everything from here to the final
        # upsert used to have NO exception handling at all — only the
        # scrape() call above was protected. Any failure here (geocoding,
        # dedup, upsert) would silently become an anonymous Exception
        # object in asyncio.gather's results, contributing only to a
        # generic "Errors: N" count in the final summary with zero
        # indication of WHICH council or WHY. Found via a real case
        # (Eastleigh Borough Council, 2026-07-21) whose own log trail
        # stopped cleanly right after "Geocoding N postcodes…" with no
        # further trace at all, and whose consecutive_empty_runs counter
        # was left frozen — neither reset by success nor incremented by
        # the genuine-empty path above, meaning execution left this
        # function some other way entirely. This doesn't change the
        # return-0-on-failure semantics already used elsewhere in this
        # function, it just makes sure a council name and real error
        # message get printed if it happens again, instead of vanishing.
        try:
            # Geocode missing coordinates
            need = [a["postcode"] for a in apps if not a.get("lat") and a.get("postcode")]
            if need:
                print(f"    Geocoding {len(set(need))} postcodes…")
                coords = await geocode(need)
                for app in apps:
                    if not app.get("lat") and app.get("postcode"):
                        pc = app["postcode"].strip().upper().replace(" ", "")
                        if pc in coords:
                            app["lat"], app["lng"] = coords[pc]

            # Council centroid fallback — same pattern as Idox
            geocoded = [(a["lat"], a["lng"]) for a in apps if a.get("lat") and a.get("lng")]
            if geocoded:
                import statistics
                centroid_lat = statistics.median(a[0] for a in geocoded)
                centroid_lng = statistics.median(a[1] for a in geocoded)
                fallback_count = 0
                for app in apps:
                    if not app.get("lat"):
                        app["lat"] = centroid_lat
                        app["lng"] = centroid_lng
                        app["geocode_quality"] = "centroid"
                        fallback_count += 1
                if fallback_count:
                    print(f"    Council centroid fallback for {fallback_count} apps")

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
                "source":           "arcus_scraper",
            } for a in apps]

            # Deduplicate by reference — the two-list councils (Epping Forest,
            # Manchester) can legitimately return the same application from
            # both their weekly-list views (e.g. one newly valid, one decided
            # this week for a fast-moving application).
            seen: set[str] = set()
            unique_records = []
            for r in records:
                if r["reference"] not in seen:
                    seen.add(r["reference"])
                    unique_records.append(r)
            records = unique_records

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
                    "coverage_source": "arcus_scraper",
                    "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                    "consecutive_empty_runs": 0,
                    "active": True,
                })
                print(f"    ✓ Saved {saved}")
            else:
                print(f"    ⚠ Partial save: {saved} of {len(apps)} (see upsert errors above)")
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    try:
        from arcus_councils import ARCUS_COUNCILS, COUNCIL_DB_IDS
    except ImportError:
        print("ERROR: arcus_councils.py not found")
        sys.exit(1)

    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Arcus scraper (Playwright)")
    print(f"Councils:    {len(ARCUS_COUNCILS)}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL else 'NOT SET'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    try:
        db_rows = await _supa_get(
            "councils",
            select="id,name,last_scraped_at",
            order="last_scraped_at.asc.nullsfirst",
            limit="600",
        )
    except Exception as e:
        print(f"Failed to fetch councils: {e}")
        sys.exit(1)

    db_by_name = {r["name"].lower(): r["id"] for r in db_rows}

    # REGISTER_URL_OVERRIDE (2026-07-21): councils whose Salesforce site
    # returns a genuine error for the standard "/register-view?c__r=
    # Arcus_BE_Public_Register" suffix — see the comment in
    # ArcusPortal.__init__ for the real evidence behind Wiltshire's entry.
    # Bare base_url (no suffix) confirmed working via manual browser test.
    REGISTER_URL_OVERRIDES: dict[str, str] = {
        "Wiltshire Council": "https://development.wiltshire.gov.uk/pr/s",
    }

    to_scrape: list[ArcusPortal] = []
    missing: list[str] = []

    for name, base_url, mode, config in ARCUS_COUNCILS:
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
            to_scrape.append(ArcusPortal(
                name, base_url, mode, config, council_id,
                register_url_override=REGISTER_URL_OVERRIDES.get(name),
            ))
        else:
            missing.append(name)

    if missing:
        print(f"Not in DB (skipping): {', '.join(missing)}\n")

    # DECISION-CADENCE FIX (2026-07-21): fetch pending backlog scoped to
    # this run's councils, same pattern as idox_scraper.py — submitted
    # 15-120 days ago (old enough to be outside the normal window, young
    # enough that still checking is realistic), paginated past
    # Supabase's default 1000-row cap, ordered oldest-first. Only
    # attached to advanced_search-mode portals below, since that's the
    # only mode with an actual date field to widen — see the comment in
    # ArcusPortal.__init__ for why the other two modes get nothing from
    # this.
    pending_by_council: dict[int, list[dict]] = {}
    recheck_lo = (date.today() - timedelta(days=120)).isoformat()
    recheck_hi = (date.today() - timedelta(days=15)).isoformat()
    council_ids_this_run = sorted({portal.db_council_id for portal in to_scrape})
    try:
        if council_ids_this_run:
            ids_csv = ",".join(str(i) for i in council_ids_this_run)
            pending_rows = []
            page_size = 1000
            for page_num in range(10):  # safety cap: 10,000 rows max
                page_rows = await _supa_get(
                    "planning_applications",
                    **{
                        "select": "council_id,reference,submitted_date",
                        "status": "eq.pending",
                        "council_id": f"in.({ids_csv})",
                        "and": f"(submitted_date.gte.{recheck_lo},submitted_date.lte.{recheck_hi})",
                        "order": "submitted_date.asc",
                        "limit": str(page_size),
                        "offset": str(page_num * page_size),
                    },
                )
                pending_rows.extend(page_rows)
                if len(page_rows) < page_size:
                    break
            for row in pending_rows:
                pending_by_council.setdefault(row["council_id"], []).append(row)
            print(f"Pending recheck: {len(pending_rows)} applications across "
                  f"{len(pending_by_council)} councils ({recheck_lo} to {recheck_hi})\n")
        for portal in to_scrape:
            portal.pending_recheck = pending_by_council.get(portal.db_council_id, [])
    except Exception as e:
        print(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}\n")

    print(f"Scraping {len(to_scrape)} councils with Playwright…\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        sem = asyncio.Semaphore(CONCURRENCY)
        # FIX (2026-07-20): previously pre-filtered here in a synchronous
        # loop before any council had actually run — see the comment in
        # process_council() for why that check was structurally
        # meaningless. Every portal now gets a task; process_council()
        # itself decides, correctly, whether the real budget has been
        # reached by the time its own turn genuinely arrives.
        tasks = [process_council(portal, browser, sem) for portal in to_scrape]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()

    total        = sum(r for r in results if isinstance(r, int))
    errors       = sum(1 for r in results if isinstance(r, Exception))
    # LOGGING FIX (2026-07-20): same fix as idox_scraper.py — these were
    # previously conflated. time_skipped is councils that genuinely never
    # got attempted because the real budget was reached; zero_result is
    # everything else that saved nothing (empty results, scrape errors
    # caught inside process_council, etc.) — a much larger and more
    # actionable bucket that was hidden inside a misleading "Skipped
    # (time)" label before, and which the old code couldn't even count
    # correctly since `skipped` was computed in a loop that no longer
    # exists.
    time_skipped = sum(1 for r in results if r == "TIME_BUDGET_SKIP")
    zero_result  = sum(1 for r in results if r == 0)

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")
    if errors:
        print(f"Errors:             {errors}")
    if time_skipped:
        print(f"Skipped (time budget):     {time_skipped} councils")
    if zero_result:
        print(f"Saved 0 (other reason — see per-council log lines above for "
              f"timeout/block/error details): {zero_result} councils")


if __name__ == "__main__":
    asyncio.run(main())
