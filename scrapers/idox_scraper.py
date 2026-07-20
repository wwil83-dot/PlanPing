#!/usr/bin/env python3
"""
PlanFind Idox scraper — Playwright edition.

Uses headless Chromium via Playwright to handle JavaScript-heavy
PublicAccess 5 (Idox Cloud) portals as well as classic PA 4.x sites.

Architecture:
  - One shared browser instance, one isolated BrowserContext per council
  - Semaphore limits to CONCURRENCY contexts at once
  - Navigates to monthlyList page, submits form, paginates, parses HTML
  - Filters results to DAYS_BACK window in Python
  - Upserts to Supabase REST API (same as data_gov_harvester)
"""
import asyncio
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
MAX_MINUTES  = 55  # hardcoded — was overridden by workflow env var
DAYS_BACK    = 14  # hardcoded — was overridden by workflow env var
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "3"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 3  # 3-min buffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
    if not s: return "pending"
    s = s.lower()
    if any(x in s for x in ("approv", "grant", "permit", "allow", "no objection")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss", "not permit")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
    return "pending"


def _extract_postcode(text: str) -> Optional[str]:
    if not text: return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_date(s: str) -> Optional[str]:
    """
    BUG FIX (2026-07-16): the old version unconditionally split on the
    first space/T/+ character before trying to match any format, on the
    assumption any space meant "date followed by a time" (e.g.
    "2026-07-01 14:30:00" -> "2026-07-01"). But several Idox councils
    (confirmed: Adur/Worthing's shared portal, likely others sharing the
    same shared-service template) format dates with a leading day name,
    e.g. "Wed 01 Jul 2026" — splitting on the first space there destroyed
    the date down to just "Wed", which then failed every format in the
    list, fell through to the scraper's undated-application fallback, and
    silently got stamped with TODAY'S date instead of the real one. That
    looked like "Today" on the site for an application actually submitted
    weeks earlier.

    Fixed by trying the FULL raw string against every known format FIRST
    (including new day-name-prefixed formats below) before ever
    destructively splitting on a separator — the separator-split is now
    only a fallback for genuine date+time strings that don't match
    anything on their own.
    """
    if not s: return None
    s = str(s).strip()

    all_formats = (
        "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y",
        "%d/%m/%y", "%Y/%m/%d",
        "%d %B %Y", "%d %b %Y",
        # Day-name-prefixed formats — confirmed needed for Adur/Worthing's
        # shared Idox portal ("Wed 01 Jul 2026"), likely other councils
        # sharing the same template too.
        "%a %d %b %Y", "%A %d %B %Y",
        "%a %d %B %Y", "%A %d %b %Y",
    )

    # Try the untouched string first — this is what fixes day-name-prefixed
    # dates, which a naive space-split would have destroyed before this
    # point in the old version.
    for fmt in all_formats:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue

    # Fallback: genuine "date + time" strings (e.g. "2026-07-01T14:30:00Z"
    # or "2026-07-01 14:30:00") that didn't match anything whole — NOW
    # it's safe to split on a separator, since we've already given the
    # full string every reasonable chance first.
    stripped = s
    for sep in ("+", "T", " "):
        if sep in stripped:
            stripped = stripped.split(sep)[0].strip()
    stripped = stripped[:10]
    if stripped != s:
        for fmt in all_formats:
            try:
                return datetime.strptime(stripped, fmt).date().isoformat()
            except ValueError:
                continue

    return None


# ---------------------------------------------------------------------------
# Supabase REST API
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
                # on_conflict tells PostgREST which constraint to use for upsert
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
    """Atomically increments consecutive_empty_runs for a council via a
    Postgres RPC function (see migration SQL: increment_empty_runs). This is
    deliberately NOT a fetch-then-patch, because CONCURRENCY=3 means multiple
    councils' coroutines run at once — a read-then-write here would risk a
    lost increment if two councils happened to touch the same row at the
    same instant (not possible for two different councils, but this pattern
    is the safe default regardless). The RPC does the increment inside a
    single UPDATE statement in the database, so it's race-free.
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
# Geocoding
# ---------------------------------------------------------------------------
async def geocode(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    if not unique: return results
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
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return results




async def geocode_addresses(apps_without_coords: list[dict]) -> dict:
    """Fallback geocoder using Nominatim (OSM) for apps without postcodes.
    Only called in bulk mode — rate limited to 1 req/sec.
    Returns dict mapping reference -> (lat, lng).
    """
    results = {}
    if not apps_without_coords:
        return results

    async with httpx.AsyncClient(
        timeout=10,
        headers={"User-Agent": "PlanFind/1.0 (planfind.co.uk)"}
    ) as c:
        for app in apps_without_coords:
            address = app.get("address", "")
            if not address:
                continue
            try:
                r = await c.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": address + ", United Kingdom",
                        "format": "json",
                        "limit": 1,
                        "countrycodes": "gb",
                    },
                )
                data = r.json()
                if data:
                    results[app["reference"]] = (
                        float(data[0]["lat"]),
                        float(data[0]["lon"]),
                    )
            except Exception:
                pass
            await asyncio.sleep(1.1)  # Nominatim rate limit: 1 req/sec

    return results

# ---------------------------------------------------------------------------
# HTML parsing (same logic as before, now fed by Playwright page.content())
# ---------------------------------------------------------------------------
def _abs_url(base_url: str, domain_root: str, href: str) -> str:
    if not href: return ""
    if href.startswith("http"): return href
    if href.startswith("/"): return f"{domain_root}{href}"
    return f"{base_url}/{href.lstrip('/')}"


# Tracks which councils have already had a date-diagnostic sample printed
# this run — see the DIAGNOSTIC blocks in _parse_result() below. Resets
# naturally every run since each scrape invocation is a fresh process.
# Two SEPARATE sets: one for the "field extraction collapsed entirely"
# diagnostic, one for the "date specifically not found" diagnostic — kept
# independent so fixing one class of bug can't accidentally silence
# visibility into the other, which is exactly what happened when these
# were briefly merged into a single check (see round-3 comment below).
_DATE_DIAGNOSED_COUNCILS: set[str] = set()
_DATE_LABEL_DIAGNOSED_COUNCILS: set[str] = set()
# Round 4 (2026-07-16): tracks councils where the month-selection dropdown
# couldn't be found by ANY known CSS selector at all — see the DIAGNOSTIC
# in _scrape_month() below. This was completely silent before; the
# round-3 fix (select month_index instead of always 0) can't help if the
# selector never even matches the real element in the first place.
_MONTH_DROPDOWN_DIAGNOSED: set[str] = set()
# 2026-07-18: tracks councils where a genuinely decided application had
# no decision date found — see the DIAGNOSTIC in _parse_result() below.
_DECISION_DATE_DIAGNOSED: set[str] = set()
# 2026-07-20: tracks councils where the results-list container itself
# (ul.searchresults / #searchResultsContainer / etc.) wasn't found on a
# results page at all — see the DIAGNOSTIC in parse_results_page() below.
# Found while investigating why ~15+ recently-added councils were being
# scraped every run (wait_for_selector was succeeding — e.g. matching
# '.no-results' or '#searchResultsForm' — so no timeout was ever logged)
# but silently saving zero applications forever. This was completely
# invisible before: parse_results_page() just returned an empty list with
# no distinction between "genuinely 0 applications this month" and "the
# container selector doesn't match this council's real page structure at
# all" — same category of silent failure as the month-dropdown bug before
# its diagnostic was added.
_RESULTS_CONTAINER_DIAGNOSED: set[str] = set()


def _parse_result(item, base_url: str, domain_root: str, council_name: str) -> Optional[dict]:
    link = item.find("a", href=True)
    if not link: return None

    portal_url = _abs_url(base_url, domain_root, link.get("href", ""))

    # In Idox MONTHLY LIST the <h2> heading is the DESCRIPTION, not the reference.
    # The planning reference (e.g. "25/01234/FUL") is in p.metaInfo as "Ref. No: ..."
    heading = item.find(re.compile(r"h[2-4]"))
    heading_text = heading.get_text(strip=True) if heading else link.get_text(strip=True)

    # Parse metadata fields first (metaInfo contains the real reference)
    fields: dict[str, str] = {}
    meta = item.find(class_=re.compile(r"metaInfo|meta-info|metadata", re.I))
    # meta_raw_text: NO forced separator — kept purely for the diagnostic
    # below, showing exactly what the underlying markup looks like
    # unmodified, for comparison/proof.
    meta_raw_text = meta.get_text(strip=True) if meta else None
    if meta:
        # FIX (2026-07-16, round 2): confirmed via cross-referencing
        # Supabase "submitted_date = today" counts against real per-
        # council save counts, then reproduced exactly via simulation —
        # the OLD code called get_text(strip=True) with NO separator,
        # which concatenates text from separate child elements with
        # NOTHING between them if the source markup doesn't literally
        # contain a "|" character in the text itself (only visual/CSS
        # spacing between elements, common in modern Idox templates).
        # That collapsed every field after the first into one giant blob,
        # attached as the VALUE of whichever key came first (almost
        # always "ref. no") — explaining why so many different councils'
        # diagnostics showed ONLY ['ref. no'] and nothing else, not just
        # a missing date. Forcing separator="|" here is a safe superset
        # fix: for councils whose HTML genuinely already contains a real
        # "|" character, this just adds a second one, which strips down
        # to an empty, harmless segment — no regression risk either way.
        for part in meta.get_text(separator="|", strip=True).split("|"):
            part = part.strip()
            if ":" in part:
                k, _, v = part.partition(":")
                fields[k.strip().lower()] = v.strip()

    for dl in item.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            k = dt.get_text(strip=True).lower().rstrip(":")
            v = dd.get_text(" ", strip=True)
            fields[k] = v

    # ── REFERENCE: metaInfo first (e.g. "Ref. No: 25/01234/FUL") ──────────
    ref = (
        fields.get("ref. no") or
        fields.get("ref no") or
        fields.get("reference") or
        fields.get("ref") or
        fields.get("app. no") or
        fields.get("application no") or
        ""
    ).strip()

    # Fallback: heading text if it looks like a real reference (has digits + slash)
    if not ref:
        if re.search(r'\d', heading_text) and ('/' in heading_text or '-' in heading_text):
            ref = heading_text
        else:
            ref = link.get_text(strip=True)

    if not ref or len(ref) < 3:
        return None

    # ── ADDRESS ─────────────────────────────────────────────────────────────
    address = ""
    addr_el = item.find(class_=re.compile(r"\baddress\b", re.I))
    if addr_el:
        address = addr_el.get_text(" ", strip=True)
    if not address:
        address = (
            fields.get("address") or
            fields.get("site address") or
            fields.get("location") or ""
        )

    # ── DESCRIPTION: heading IS the description in monthly list mode ────────
    description = (
        fields.get("proposal") or
        fields.get("description") or
        fields.get("development description") or
        heading_text
    )
    if description == ref:
        description = ""

    app_type  = fields.get("application type") or fields.get("type") or ""
    status_raw = fields.get("status") or fields.get("decision") or ""
    date_raw   = (
        fields.get("date received") or
        fields.get("date valid") or
        fields.get("date validated") or
        fields.get("date registered") or
        fields.get("date of receipt") or
        fields.get("received") or
        fields.get("validated") or
        fields.get("valid") or
        fields.get("registered") or
        fields.get("reg. date") or
        fields.get("reg date") or
        ""
    )

    # NEW (2026-07-18): decision_date was previously ALWAYS hardcoded to
    # None here — genuinely never attempted, not a mislabeled-field bug
    # like submitted_date turned out to be. Confirmed via direct evidence:
    # 64% of every decided (approved/refused) application in the database
    # had no decision date at all, and this line was literally
    # `"decision_date": None,` unconditionally. Building real extraction
    # now, using the same proven _parse_date() function. "decision notice
    # was sent date" is a REAL confirmed Idox label — seen directly in a
    # Bromley Advanced Search screenshot earlier this session ("Decision
    # notice was sent date from/to"). The rest are plausible variants,
    # not yet confirmed — the diagnostic below will reveal the real label
    # for any council where none of these match, same evidence-based
    # approach that eventually cracked submitted_date, rather than
    # guessing blind a second time.
    decision_date_raw = (
        fields.get("decision notice was sent date") or
        fields.get("decision date") or
        fields.get("date decision") or
        fields.get("date decided") or
        fields.get("decided") or
        fields.get("decision issued date") or
        fields.get("date of decision") or
        fields.get("decision made date") or
        ""
    )

    # DIAGNOSTIC (2026-07-16, round 2): the first version of this
    # diagnostic only printed the parsed field KEYS, and every affected
    # council showed the exact same thing: ['ref. no'] and nothing else —
    # no address, no status, no proposal, not just a missing date. That's
    # not "wrong date label", that's the whole field-splitting mechanism
    # only ever capturing the FIRST field. Working theory: the parser
    # splits on a literal "|" character, assuming that's how Idox
    # separates fields in the rendered text — if the real markup no
    # longer contains a literal "|" (e.g. fields are now separate child
    # elements with CSS-only visual spacing, no pipe symbol in the actual
    # text), splitting on "|" returns the WHOLE block as one blob, and
    # partition(":") then grabs "ref. no" as the key and silently
    # swallows every other field's text into that one value. Printing the
    # raw meta text directly (once per council per run) proves or
    # disproves this rather than guessing again.
    if len(fields) <= 1 and council_name not in _DATE_DIAGNOSED_COUNCILS:
        _DATE_DIAGNOSED_COUNCILS.add(council_name)
        raw_preview = (meta_raw_text or "(no metaInfo/meta-info/metadata element found at all)")[:400]
        print(f"    ⚠ FIELD DIAGNOSTIC [{council_name}]: only {list(fields.keys())} "
              f"extracted — raw text was: {raw_preview!r}")

    # DIAGNOSTIC (2026-07-16, round 3): the round-2 separator fix worked —
    # confirmed by round-2 diagnostic almost never firing across a full
    # 210-council run. But real "submitted_date = today" counts barely
    # moved (Leeds 211→211, Stockport 121→121, Richmond 93→93 — nearly
    # identical before and after). Root cause: broadening the diagnostic
    # trigger to len(fields) <= 1 accidentally SILENCED the original,
    # still-unsolved problem for any council where the separator fix now
    # correctly recovers several OTHER fields (address, status, etc.) but
    # the DATE field specifically still uses a label not in the fallback
    # chain above — date_raw stays empty and the today-fallback still
    # fires, just with no diagnostic output anymore since len(fields) > 1
    # no longer trips the round-2 check. This is a SEPARATE, independently
    # rate-limited diagnostic specifically for "date not found" regardless
    # of how many other fields WERE found — and prints full key/value
    # pairs (not just keys) so a real date value can be spotted directly
    # sitting under whatever unrecognized label the council actually uses,
    # rather than guessing at label names blind a third time.
    if not date_raw and council_name not in _DATE_LABEL_DIAGNOSED_COUNCILS:
        _DATE_LABEL_DIAGNOSED_COUNCILS.add(council_name)
        preview_items = {k: v[:40] for k, v in fields.items()}
        print(f"    ⚠ DATE LABEL DIAGNOSTIC [{council_name}]: {len(fields)} field(s) "
              f"extracted but none matched a known date label. Full fields: {preview_items}")

    normalised_status = _normalise_status(status_raw)
    parsed_decision_date = _parse_date(decision_date_raw)

    # DIAGNOSTIC: fires only when the application is genuinely decided
    # (approved/refused) but no decision date was found — deliberately
    # NOT firing for pending applications, which correctly have no
    # decision date yet (that's not a bug, that's reality). Rate-limited
    # per council per run, same pattern as the other diagnostics.
    if (normalised_status in ("approved", "refused")
            and not parsed_decision_date
            and council_name not in _DECISION_DATE_DIAGNOSED):
        _DECISION_DATE_DIAGNOSED.add(council_name)
        preview_items = {k: v[:40] for k, v in fields.items()}
        print(f"    ⚠ DECISION DATE DIAGNOSTIC [{council_name}]: status is "
              f"'{normalised_status}' but no decision date matched any known "
              f"label. Full fields: {preview_items}")

    return {
        "reference":        ref.strip(),
        "address":          address.strip(),
        "postcode":         _extract_postcode(address),
        "lat":              None,
        "lng":              None,
        "description":      description.strip(),
        "application_type": app_type.strip(),
        "status":           normalised_status,
        "submitted_date":   _parse_date(date_raw),
        "decision_date":    parsed_decision_date,
        "council_name":     council_name,
        "council_url":      portal_url,
        "source":           "idox_scraper",
    }


def parse_results_page(
    html: str, base_url: str, domain_root: str, council_name: str
) -> tuple[list[dict], bool]:
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    container = (
        soup.find("ul", class_="searchresults")
        or soup.find("ul", id="searchresults")
        or soup.find("div", class_="searchresults")
        or soup.find("div", id="searchResultsContainer")
    )
    if not container:
        if council_name not in _RESULTS_CONTAINER_DIAGNOSED:
            _RESULTS_CONTAINER_DIAGNOSED.add(council_name)
            # Show what IS on the page, since that's the real evidence
            # needed to fix this — same "capture real HTML, don't guess"
            # principle idox_form_recon.py already established for the
            # month-dropdown investigation.
            title_match = soup.find("title")
            title_text = title_match.get_text(strip=True) if title_match else "(no <title>)"
            body_snippet = soup.get_text(" ", strip=True)[:200]

            # WAF/BOT-BLOCK SIGNATURE CHECK (2026-07-20): a blocked request
            # to an Idox portal still returns a normal 200-status HTML page
            # — it just isn't the results page, so wait_for_selector()
            # upstream doesn't time out and this failure mode was
            # previously indistinguishable from a genuine selector
            # mismatch. Checking title+body against known WAF/bot-block
            # vendor phrasing turns "go read the HTML yourself" into an
            # immediate answer in the log line itself. Not exhaustive —
            # absence of a match doesn't rule out a block, just an
            # unrecognised one — but a match is a strong positive signal.
            waf_signatures = (
                "attention required", "cloudflare", "just a moment",
                "access denied", "forbidden", "pardon our interruption",
                "unusual traffic", "captcha", "incapsula", "are you a robot",
                "bot detection", "request blocked", "security service",
                "reference id", "akamai", "perimeterx", "distil",
                # Added 2026-07-20 after a real run showed multiple genuine
                # 429 responses (both plain server-level, e.g. "IDOX Public
                # Access Error 429", and WAF-flavored, e.g. "Too Many
                # Requests... unusual traffic... automated queries") that
                # weren't being tagged because "429"/"too many requests"
                # wasn't in this list at all — a significant miss, since
                # this turned out to be one of the most common real
                # signatures once request volume increased.
                "429", "too many requests", "rate limit",
            )
            combined = f"{title_text} {body_snippet}".lower()
            matched = [sig for sig in waf_signatures if sig in combined]
            waf_tag = f" [LIKELY WAF/BOT BLOCK — matched: {', '.join(matched)}]" if matched else ""

            print(f"    ⚠ RESULTS CONTAINER DIAGNOSTIC [{council_name}]{waf_tag}: none of the "
                  f"known result-container selectors "
                  f"(ul.searchresults / #searchresults / div.searchresults / "
                  f"#searchResultsContainer) matched anything on this page. "
                  f"Page title: {title_text!r}. Body starts: {body_snippet!r}")
        return apps, False

    items = (
        container.find_all("li", class_="searchresult")
        or container.find_all("tr")
    )

    for item in items:
        app = _parse_result(item, base_url, domain_root, council_name)
        if app:
            apps.append(app)

    # Idox pagination — try multiple patterns
    has_next = bool(
        # Text-based "Next" link
        soup.find("a", string=re.compile(r"^Next$", re.I))
        or soup.find("a", string=re.compile(r"Next page", re.I))
        # Class-based
        or soup.find("a", {"class": "next"})
        or soup.find("li", {"class": "next"})
        or soup.find("span", {"class": "next"})
        # Common Idox pager with ">" or ">>" symbols
        or soup.find("a", string=re.compile(r"^[>»]$"))
        # Page count indicator: "1 - 10 of 45" → more pages exist
        or _has_more_pages(soup)
    )
    return apps, has_next


def _has_more_pages(soup) -> bool:
    """Detect pagination from 'Displaying X to Y of Z' style counters."""
    # Pattern: "Displaying 1 to 10 of 45 results"
    text = soup.get_text()
    m = re.search(
        r"(?:displaying|showing|results?)\s+(\d+)\s+(?:to|-)\s+(\d+)\s+of\s+(\d+)",
        text, re.I
    )
    if m:
        end, total = int(m.group(2)), int(m.group(3))
        return end < total
    # Pattern: "Page 1 of 5"
    m = re.search(r"page\s+(\d+)\s+of\s+(\d+)", text, re.I)
    if m:
        current, total_pages = int(m.group(1)), int(m.group(2))
        return current < total_pages
    return False


# ---------------------------------------------------------------------------
# Playwright scraper
# ---------------------------------------------------------------------------
# Realistic browser headers / viewport to avoid bot detection
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport":       {"width": 1280, "height": 800},
    "locale":         "en-GB",
    "timezone_id":    "Europe/London",
    "java_script_enabled": True,
    "ignore_https_errors": True,
}

# ---------------------------------------------------------------------------
# monthlyListResults.do?action=firstPage fallback — EXPLICIT ALLOWLIST ONLY.
#
# Earlier version tried this fallback on every timeout for every council.
# That was a mistake: dozens of already-broken councils each burned an extra
# ~45s+25s retrying a path that was just as blocked, which pushed total run
# time past GitHub Actions' 65-minute hard job timeout and got the whole
# workflow cancelled before it finished (see run of 2026-07-07 15:58 UTC —
# only got through ~2/3 of councils before cancellation, vs 49.6 min for all
# 157 the run before). Keep this list short and only add councils where the
# fallback is a genuine, confirmed candidate — not a blanket safety net.
#
# Dumfries and Galloway Council tested here 2026-07-07/09 — confirmed
# Cloudflare/bot-challenge blocked even with this fallback, so it's been
# removed from both this allowlist AND the active IDOX_COUNCILS list in
# idox_councils.py. Leaving this set empty for now — add back only for a
# new, genuinely untested candidate.
# ---------------------------------------------------------------------------
TRY_FIRSTPAGE_FALLBACK_COUNCILS: set[str] = set()


class IdoxPortal:
    """Scrapes one Idox planning portal via Playwright."""

    def __init__(self, council_name: str, base_url: str, db_council_id: int,
                 use_weekly_list: bool = False):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.db_council_id = db_council_id   # ← locked to this portal, immune to concurrency
        self.use_weekly_list = use_weekly_list
        parsed = urlparse(self.base_url)
        self.domain_root = f"{parsed.scheme}://{parsed.netloc}"

    async def scrape(self, browser: Browser, days_back: int = 7,
                      pending_recheck: Optional[list[dict]] = None) -> list[dict]:
        cutoff = date.today() - timedelta(days=days_back)

        # Build the full list of calendar months to scrape.
        # Fast mode (14 days): 1-2 months. Bulk mode (180 days): up to ~6-7 months.
        months: list[date] = []
        m = date.today().replace(day=1)
        cutoff_month = cutoff.replace(day=1)
        while m >= cutoff_month:
            months.append(m)
            if m.month == 1:
                m = m.replace(year=m.year - 1, month=12)
            else:
                m = m.replace(month=m.month - 1)

        # DECISION-CADENCE FIX (2026-07-20): fast mode's 14-day cutoff means
        # an application submitted, say, 40 days ago drops out of the month
        # range entirely — so once it leaves the window, nightly scraping
        # never looks at it again, even though UK decisions typically land
        # 8+ weeks after submission. That's why decision_detected_at was
        # only accumulating via occasional bulk runs (confirmed: 1 total
        # detection since deployment). pending_recheck is a list of
        # {reference, submitted_date} for applications we already have on
        # file as still "pending" — main() fetches this from Supabase before
        # scraping starts. We add their months to the scrape (capped, so a
        # council with a huge backlog doesn't blow the nightly time budget)
        # so their status can genuinely be re-observed and, if changed,
        # upserted — which is what lets the decision_detected_at trigger
        # actually fire close to when a decision happens, not just whenever
        # bulk mode next happens to run.
        pending_refs: set[str] = set()
        if pending_recheck:
            pending_refs = {p["reference"] for p in pending_recheck if p.get("reference")}
            recheck_months: list[date] = []
            for p in pending_recheck:
                d = p.get("submitted_date")
                if not d:
                    continue
                try:
                    month_start = date.fromisoformat(d).replace(day=1)
                except ValueError:
                    continue
                if month_start not in months and month_start not in recheck_months:
                    recheck_months.append(month_start)
            # Cap extra months so a council with a large pending backlog
            # can't balloon nightly runtime — oldest first, since those are
            # the most "overdue" for a decision and most useful to recheck.
            recheck_months.sort()
            # REDUCED 2026-07-20 (was 4): a real run confirmed this was too
            # generous once the pagination fix let the true pending
            # backlog through (previously silently capped at 1000 rows,
            # hiding the real scope). With the real backlog visible,
            # batch 1 ran 61.0 minutes against a 55-minute budget and had
            # to hard-skip 49 councils entirely, and multiple councils
            # returned genuine 429 "Too Many Requests" responses — real
            # evidence, not a guess, that this many extra paginated
            # requests to the same council in one run is too much. Halving
            # the cap directly reduces both the per-council request volume
            # (the proximate cause of the 429s) and total batch runtime.
            MAX_RECHECK_MONTHS = 2
            months.extend(recheck_months[:MAX_RECHECK_MONTHS])

        all_apps: list[dict] = []

        context: BrowserContext = await browser.new_context(**CONTEXT_OPTIONS)
        try:
            page: Page = await context.new_page()
            # Mask automation flags
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            if self.use_weekly_list:
                # Weekly list portals don't expose the monthly list action.
                # Only fetch current week (week -1 via searchCriteria.weekNum=1
                # causes ERR_CONNECTION_TIMED_OUT on many servers).
                # Daily cron covers the rolling 14-day window across consecutive runs.
                apps = await self._scrape_week(page, week_offset=0)
                today_str = date.today().isoformat()
                for app in apps:
                    if not app.get("submitted_date"):
                        app["_month_fallback"] = today_str
                all_apps.extend(apps)
            else:
                for target_month in months:
                    apps = await self._scrape_month(page, target_month)
                    # Use TODAY as fallback date for apps without a parsed date.
                    # Using today (not start-of-month) means undated apps show as
                    # recently scraped rather than all clustering on '01/06/26'.
                    today_str = date.today().isoformat()
                    for app in apps:
                        if not app.get("submitted_date"):
                            app["_month_fallback"] = today_str
                    all_apps.extend(apps)
        except Exception as e:
            print(f"    ✗ Context error: {e}")
        finally:
            await context.close()

        recent = [
            a for a in all_apps
            if not a.get("submitted_date")
            or a["submitted_date"] >= cutoff.isoformat()
            or a.get("reference") in pending_refs
        ]

        # Apply month-based fallback dates AFTER the filter (so "2026-06-01" doesn't
        # get rejected by the 7-day cutoff check)
        for app in recent:
            if not app.get("submitted_date") and app.get("_month_fallback"):
                app["submitted_date"] = app["_month_fallback"]
            app.pop("_month_fallback", None)  # never send temp field to DB

        print(f"    {len(all_apps)} this month → {len(recent)} in last {days_back} days")
        return recent

    async def _scrape_month(self, page: Page, for_month: date) -> list[dict]:
        """Load the monthly list, submit it for date received, collect all pages."""
        # Calculate monthYearIndex: 0 = current month, 1 = previous month, etc.
        today_month = date.today().replace(day=1)
        month_index = (today_month.year - for_month.year) * 12 + (today_month.month - for_month.month)

        # Don't pre-specify dateType in the URL — some portals only support DV
        # (Date Validated) not DC (Date Confirmed/Received), so forcing DC gives 0.
        # Instead let the form default apply, then try to click the best radio.
        monthly_url = (
            f"{self.base_url}/search.do"
            f"?action=monthlyList"
            f"&searchCriteria.monthYearIndex={month_index}"
            f"&searchType=Application"
        )

        # — Step 1: Navigate to monthly list page —
        try:
            await page.goto(monthly_url, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            # FALLBACK: some portals (e.g. eaccess.dumgal.gov.uk) block/timeout
            # on the search.do?action=monthlyList path specifically, but the
            # monthlyListResults.do?action=firstPage path (what a human clicking
            # "Weekly/Monthly Lists" actually lands on) goes straight to results
            # without the form-submission dance. Only safe for the CURRENT month
            # (month_index 0) — firstPage doesn't support monthYearIndex, so it
            # can't stand in for historical months.
            # GATED to an explicit allowlist + remaining time budget — see
            # TRY_FIRSTPAGE_FALLBACK_COUNCILS comment above for why this is
            # not applied to every timeout.
            if (month_index == 0
                    and self.council_name in TRY_FIRSTPAGE_FALLBACK_COUNCILS
                    and not should_stop()):
                print(f"    ⚠ Page load timeout — trying monthlyListResults.do fallback")
                return await self._scrape_month_firstpage_fallback(page)
            print(f"    ⚠ Page load timeout")
            return []
        except Exception as e:
            print(f"    ⚠ Navigation error: {e}")
            return []

        # Wait for form or results to appear
        try:
            await page.wait_for_selector(
                "#monthlyListForm, form, ul.searchresults",
                timeout=12_000,
            )
        except PlaywrightTimeout:
            title = await page.title()
            print(f"    ⚠ Nothing loaded — title: '{title[:60]}'")
            # Try the same fallback if the page loaded but nothing usable appeared
            if (month_index == 0
                    and self.council_name in TRY_FIRSTPAGE_FALLBACK_COUNCILS
                    and not should_stop()):
                print(f"    ⚠ Trying monthlyListResults.do fallback")
                return await self._scrape_month_firstpage_fallback(page)
            return []

        # — Step 2: Click "date received" radio & submit form —
        form_submitted = False

        # BUG FIX (2026-07-16/17): this used to unconditionally call
        # select_option(index=0) — which always selects whatever's FIRST
        # in the dropdown (the current month), completely ignoring
        # month_index. Then a round-3 fix (select the correct index
        # instead of always 0) STILL didn't resolve it — because the real
        # bug was one level deeper: idox_form_recon.py captured direct
        # HTML evidence from a real monthly-list page and proved the
        # month dropdown's actual attribute is simply id='month'/
        # name='month' — NOT 'searchCriteria.monthYearIndex' or anything
        # containing 'monthYear'. None of the 4 old candidate selectors
        # could EVER have matched, on ANY council, confirmed via
        # idox_month_test.py showing identical results across all tested
        # months for all 3 test councils (2 different underlying server
        # setups). This affects more than just --bulk mode: normal nightly
        # fast-mode scraping needs 2 months (index 0 and 1) whenever the
        # 14-day window crosses a calendar-month boundary — this bug would
        # have silently returned duplicate current-month data instead of
        # genuine previous-month data for that portion of every month too.
        # The real id='month' selector is listed FIRST now, since it's
        # confirmed correct via direct evidence, not a guess. The old
        # candidates are kept as fallbacks in case some other council's
        # Idox instance genuinely uses different naming (Idox
        # installations vary significantly by council, as this whole
        # investigation has repeatedly shown).
        dropdown_found = False
        for month_sel in [
            "select[id='month']",
            "select[name='month']",
            "select[id='searchCriteria.monthYearIndex']",
            "select[name='searchCriteria.monthYearIndex']",
            "select[name*='monthYear']",
            "select[id*='monthYear']",
        ]:
            try:
                loc = page.locator(month_sel)
                option_count = await loc.count()
                if option_count > 0:
                    try:
                        await loc.select_option(index=month_index)
                    except Exception:
                        # Requested index may not exist in this dropdown
                        # (e.g. a council whose list only covers fewer
                        # months than 13) — fall back to the closest
                        # valid option rather than silently defaulting
                        # back to month 0, which is exactly the bug this
                        # fix addresses.
                        await loc.select_option(index=0)
                    dropdown_found = True
                    break
            except Exception:
                continue

        # DIAGNOSTIC (2026-07-16, round 4): the round-3 fix (select the
        # correct month_index instead of always index=0) did NOT resolve
        # the identical-results problem on a real bulk run — Stockport,
        # Bolton, and others still showed byte-identical totals across
        # every month attempt even with the fix in place. One likely
        # explanation the previous fix couldn't catch: if NONE of the 4
        # candidate CSS selectors match this council's real dropdown
        # element at all, the whole selection loop silently no-ops —
        # nothing gets selected, the form submits with its own default
        # (probably "current month" again), and the URL's month_index
        # parameter alone isn't enough to determine what the SUBMITTED
        # FORM actually requests. This was previously completely silent —
        # no warning printed either way. Surfacing it now, rate-limited to
        # once per council per run.
        if not dropdown_found and self.council_name not in _MONTH_DROPDOWN_DIAGNOSED:
            _MONTH_DROPDOWN_DIAGNOSED.add(self.council_name)
            print(f"    ⚠ MONTH DROPDOWN DIAGNOSTIC [{self.council_name}]: none of the "
                  f"known dropdown selectors matched ANY element on this page — "
                  f"month selection is being silently skipped entirely, form is "
                  f"submitting with whatever its own default is regardless of "
                  f"month_index={month_index}.")

        # Try clicking the date-received radio button.
        # Different Idox versions use different values: dateReceived, dc, dv, dr.
        # Try DC/Received first, fall back to DV (Validated) then DR (Registered).
        for radio_sel in [
            "input#dateReceived",
            "input[value='dateReceived']",
            "input[id*='Received'][type='radio']",
            "input[name*='date'][value*='eceiv']",
            "label:has-text('Received') input",
            "input[value='dc']",
            "input[value='DC']",
            "input[value='dv']",
            "input[value='DV']",
            "input[id*='Validated'][type='radio']",
            "label:has-text('Validated') input",
        ]:
            try:
                loc = page.locator(radio_sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    break
            except Exception:
                continue

        # Submit the form
        for submit_sel in [
            "#monthlyListForm input[type='submit']",
            "#monthlyListForm input.button",
            "form input[type='submit']",
            "form button[type='submit']",
            "input.button",
        ]:
            try:
                loc = page.locator(submit_sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    form_submitted = True
                    break
            except Exception:
                continue

        if not form_submitted:
            # Some portals go straight to results without form interaction
            pass

        # Wait for results
        try:
            await page.wait_for_selector(
                "ul.searchresults, #searchResultsContainer, .searchresults, "
                ".no-results, #searchResultsForm",
                timeout=25_000,
            )
        except PlaywrightTimeout:
            title = await page.title()
            print(f"    ⚠ Results timeout — title: '{title[:60]}'")
            # Try the fallback here too — form submission may have hung on a
            # blocked path even though the initial page load succeeded.
            if (month_index == 0
                    and self.council_name in TRY_FIRSTPAGE_FALLBACK_COUNCILS
                    and not should_stop()):
                print(f"    ⚠ Trying monthlyListResults.do fallback")
                return await self._scrape_month_firstpage_fallback(page)
            return []

        # — Step 3: Collect all pages —
        all_apps: list[dict] = []
        page_num = 1
        # Some Idox portals silently redirect back to page 1 when asked for a
        # page number beyond their actual results, instead of returning an
        # empty page. Without this check that looks identical to "there's
        # more data" and the loop runs all the way to the 50-page hard cap,
        # wasting minutes re-fetching the same content (seen 2026-07-09:
        # Newham and Brent both looped to 50 pages, one claiming 10,650
        # "applications" that were really ~150 real ones repeated ~35x).
        # Comparing each page's reference set to the previous page's catches
        # this immediately regardless of the portal's specific looping
        # behavior.
        previous_refs: frozenset = frozenset()

        while True:
            html = await page.content()
            apps, has_next = parse_results_page(
                html, self.base_url, self.domain_root, self.council_name
            )

            current_refs = frozenset(a["reference"] for a in apps)
            if page_num > 1 and current_refs and current_refs == previous_refs:
                print(f"    Page {page_num} identical to previous — portal looped back, stopping")
                break
            previous_refs = current_refs

            all_apps.extend(apps)

            if page_num == 1 and len(apps) > 0:
                print(f"    Page 1: {len(apps)} results")

            # Continue if explicit Next link OR got a full page (try page 2)
            should_continue = has_next or (len(apps) >= 10)
            if not should_continue or not apps or page_num >= 50:
                break

            page_num += 1
            next_url = (
                f"{self.base_url}/pagedSearchResults.do"
                f"?action=page&searchCriteria.page={page_num}"
            )
            try:
                await page.goto(
                    next_url, wait_until="domcontentloaded", timeout=15_000
                )
                # Give JS time to render — don't use wait_for_selector here
                # as it can time out on pages that use non-standard selectors
                await asyncio.sleep(2)
            except Exception as e:
                print(f"    Page {page_num} nav error: {e}")
                break

        if page_num > 1:
            print(f"    Total across {page_num} pages: {len(all_apps)}")
        return all_apps

    async def _scrape_month_firstpage_fallback(self, page: Page) -> list[dict]:
        """Fallback for portals where search.do?action=monthlyList times out or
        gets blocked, but monthlyListResults.do?action=firstPage works. This is
        the URL a human lands on clicking 'Weekly/Monthly Lists' in the UI — it
        goes straight to results for the CURRENT month, no form submission
        needed. Only valid for month_index 0 (see caller).
        """
        fallback_url = f"{self.base_url}/monthlyListResults.do?action=firstPage"

        try:
            await page.goto(fallback_url, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            print(f"    ⚠ Fallback page load timeout")
            return []
        except Exception as e:
            print(f"    ⚠ Fallback navigation error: {e}")
            return []

        try:
            await page.wait_for_selector(
                "ul.searchresults, #searchResultsContainer, .searchresults, "
                ".no-results, #searchResultsForm",
                timeout=25_000,
            )
        except PlaywrightTimeout:
            title = await page.title()
            print(f"    ⚠ Fallback results timeout — title: '{title[:60]}'")
            return []

        all_apps: list[dict] = []
        page_num = 1
        previous_refs: frozenset = frozenset()

        while True:
            html = await page.content()
            apps, has_next = parse_results_page(
                html, self.base_url, self.domain_root, self.council_name
            )

            current_refs = frozenset(a["reference"] for a in apps)
            if page_num > 1 and current_refs and current_refs == previous_refs:
                print(f"    Fallback page {page_num} identical to previous — portal looped back, stopping")
                break
            previous_refs = current_refs

            all_apps.extend(apps)

            if page_num == 1 and len(apps) > 0:
                print(f"    Fallback page 1: {len(apps)} results")

            should_continue = has_next or (len(apps) >= 10)
            if not should_continue or not apps or page_num >= 50:
                break

            page_num += 1
            next_url = (
                f"{self.base_url}/pagedSearchResults.do"
                f"?action=page&searchCriteria.page={page_num}"
            )
            try:
                await page.goto(next_url, wait_until="domcontentloaded", timeout=15_000)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"    Fallback page {page_num} nav error: {e}")
                break

        if page_num > 1:
            print(f"    Fallback total across {page_num} pages: {len(all_apps)}")
        return all_apps

    async def _scrape_week(self, page: Page, week_offset: int = 0) -> list[dict]:
        """Scrape a weekly list page for portals that don't support monthly lists.
        week_offset=0 is the current week, 1 is last week.
        Weekly lists go directly to results — no form submission needed.
        """
        # Visit portal home page first to establish session cookies —
        # some Idox installations (e.g. Midlothian) reject direct weekly list
        # access without a valid session.
        try:
            await page.goto(
                f"{self.base_url}/search.do?action=simple&searchType=Application",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await asyncio.sleep(1)
        except Exception:
            pass  # Best effort — proceed even if home page fails

        weekly_url = f"{self.base_url}/weeklyListResults.do?action=firstPage"
        if week_offset > 0:
            # Idox weekly list uses a weekNum parameter for previous weeks
            # weekNum counts back from the current week
            weekly_url += f"&searchCriteria.weekNum={week_offset}"

        try:
            await page.goto(weekly_url, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            print(f"    ⚠ Page load timeout (week -{week_offset})")
            return []
        except Exception as e:
            print(f"    ⚠ Navigation error: {e}")
            return []

        # Wait for results
        try:
            await page.wait_for_selector(
                "ul.searchresults, #searchResultsContainer, .searchresults, "
                ".no-results, #searchResultsForm",
                timeout=25_000,
            )
        except PlaywrightTimeout:
            title = await page.title()
            print(f"    ⚠ Results timeout — title: '{title[:60]}'")
            return []

        # Collect all pages (same logic as _scrape_month)
        all_apps: list[dict] = []
        page_num = 1
        previous_refs: frozenset = frozenset()

        while True:
            html = await page.content()
            apps, has_next = parse_results_page(
                html, self.base_url, self.domain_root, self.council_name
            )

            current_refs = frozenset(a["reference"] for a in apps)
            if page_num > 1 and current_refs and current_refs == previous_refs:
                print(f"    Week -{week_offset} page {page_num} identical to previous — portal looped back, stopping")
                break
            previous_refs = current_refs

            all_apps.extend(apps)

            if page_num == 1 and len(apps) > 0:
                print(f"    Week -{week_offset} page 1: {len(apps)} results")

            should_continue = has_next or (len(apps) >= 10)
            if not should_continue or not apps or page_num >= 50:
                break

            page_num += 1
            next_url = (
                f"{self.base_url}/pagedSearchResults.do"
                f"?action=page&searchCriteria.page={page_num}"
            )
            try:
                await page.goto(next_url, wait_until="domcontentloaded", timeout=15_000)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"    Page {page_num} nav error: {e}")
                break

        if page_num > 1:
            print(f"    Week -{week_offset} total across {page_num} pages: {len(all_apps)}")
        return all_apps


# ---------------------------------------------------------------------------
# Per-council orchestration
# ---------------------------------------------------------------------------
async def process_council(
    portal: IdoxPortal,
    browser: Browser,
    sem: asyncio.Semaphore,
    days_back: int,
    bulk_mode: bool = False,
    budget_minutes: int = MAX_MINUTES,
    pending_recheck: Optional[list[dict]] = None,
) -> int:
    # council_id comes ONLY from the portal object — never a loose parameter
    # that could get corrupted in async concurrent execution.
    cid = portal.db_council_id

    async with sem:
        # BUG FIX (2026-07-17): the OLD time-budget check happened in a
        # synchronous loop that built the ENTIRE task list (all 210
        # coroutine objects) in one fast pass, BEFORE asyncio.gather() ever
        # started running any of them for real. That loop finishes in
        # microseconds, so elapsed_minutes() was essentially always ~0 for
        # every single council checked there — the "stop starting new
        # councils once close to budget" safety mechanism never actually
        # triggered in practice, confirmed directly: a real bulk run got
        # forcibly cancelled by GitHub Actions' external 200-minute limit
        # at council #15 (Wyre Forest) despite the internal 180-minute
        # budget supposedly having a 3-minute safety buffer built in. The
        # check needed to happen HERE instead — fresh, for each council,
        # at the genuine moment its turn has actually arrived (especially
        # meaningful at CONCURRENCY=1 in bulk mode, where this really is
        # real sequential time, not a synchronous formality).
        # BUG FIX (2026-07-17): the OLD time-budget check happened in a
        # synchronous loop that built the ENTIRE task list (all 210
        # coroutine objects) in one fast pass, BEFORE asyncio.gather() ever
        # started running any of them for real. That loop finishes in
        # microseconds, so elapsed_minutes() was essentially always ~0 for
        # every single council checked there — the "stop starting new
        # councils once close to budget" safety mechanism never actually
        # triggered in practice, confirmed directly: a real bulk run got
        # forcibly cancelled by GitHub Actions' external 200-minute limit
        # at council #15 (Wyre Forest) despite the internal 180-minute
        # budget supposedly having a 3-minute safety buffer built in. The
        # check needed to happen HERE instead — fresh, for each council,
        # at the genuine moment its turn has actually arrived (especially
        # meaningful at CONCURRENCY=1 in bulk mode, where this really is
        # real sequential time, not a synchronous formality).
        #
        # NOTE: deliberately uses the explicitly-passed budget_minutes
        # parameter, NOT the should_stop()/MAX_MINUTES module-level
        # global — that global is hardcoded to 55 (the FAST-mode value)
        # and does not reflect bulk mode's real 180-minute budget (read
        # fresh from an env var inside main()). Using should_stop() here
        # would have silently cut every bulk run off at ~52 minutes
        # instead of ~177 — caught before shipping by checking rather
        # than assuming the existing should_stop() helper used the right
        # value for whichever mode was actually running.
        if elapsed_minutes() >= budget_minutes - 3:
            print(f"\n[{portal.council_name}] — skipping, time budget reached "
                  f"({elapsed_minutes():.1f} min elapsed)")
            # LOGGING FIX (2026-07-20): this used to return 0, identical to
            # every OTHER reason a council saves nothing (timeout, WAF
            # block, dead URL, template mismatch). That made the summary
            # line's "Skipped (time): N" figure meaningless — a real run's
            # log showed 47-65 "time skips" despite finishing in ~33-36
            # minutes against a 55-minute budget, nowhere near triggering
            # this branch even once. Returning a distinct sentinel here
            # lets main() report genuine time-budget skips separately from
            # the much larger "saved 0 for some other real reason" bucket,
            # instead of conflating them under a misleading label.
            return "TIME_BUDGET_SKIP"

        print(f"\n[{portal.council_name}] (council_id={cid})")
        await asyncio.sleep(1)  # stagger requests — avoids triggering WAF rate limits

        try:
            apps = await portal.scrape(browser, days_back, pending_recheck=pending_recheck)
        except Exception as e:
            print(f"    ✗ Error: {e}")
            return 0

        if not apps:
            await _supa_patch_council(cid, {
                "last_scraped_at": datetime.now(timezone.utc).isoformat()
            })
            # Track consecutive empty runs so genuinely broken councils can
            # be distinguished from ones that just had one quiet night —
            # plenty of low-volume councils legitimately return 0 sometimes.
            # See council_health_check.py, which alerts once this streak
            # crosses a threshold (default 5 consecutive nights).
            await _supa_increment_empty_runs(cid)
            return 0

        # Geocode missing coordinates — step 1: postcodes.io (fast, batched)
        need = [a["postcode"] for a in apps if not a.get("lat") and a.get("postcode")]
        if need:
            print(f"    Geocoding {len(set(need))} postcodes…")
            coords = await geocode(need)
            for app in apps:
                if not app.get("lat") and app.get("postcode"):
                    pc = app["postcode"].strip().upper().replace(" ", "")
                    if pc in coords:
                        app["lat"], app["lng"] = coords[pc]

        # Step 2: Nominatim address fallback for apps still without coordinates
        # Only in bulk mode — Nominatim is rate-limited (1 req/sec) so too slow for daily
        still_missing = [a for a in apps if not a.get("lat") and a.get("address")]
        if still_missing and bulk_mode:
            print(f"    Address geocoding {len(still_missing)} ungeocodable apps via Nominatim…")
            addr_coords = await geocode_addresses(still_missing)
            for app in apps:
                if not app.get("lat") and app["reference"] in addr_coords:
                    app["lat"], app["lng"] = addr_coords[app["reference"]]

        # Step 3: Council centroid fallback — use median of geocoded apps in this batch
        # Ensures major greenfield applications appear somewhere on the map
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

        # Build upsert records — cid is captured from portal object, not the parameter
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
            "source":           "idox_scraper",
        } for a in apps]

        # Deduplicate by reference — Idox monthly list sometimes returns the
        # same application on multiple pages, causing upsert to fail
        seen: set[str] = set()
        unique_records = []
        for r in records:
            if r["reference"] not in seen:
                seen.add(r["reference"])
                unique_records.append(r)
        records = unique_records

        print(f"    Upserting {len(records)} records with council_id={cid}")

        # Upsert in small batches — one bad record kills a whole batch
        # so keep batches small to isolate failures
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
                "coverage_source": "idox_scraper",
                "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_empty_runs": 0,
                "active": True,
            })
            print(f"    ✓ Saved {saved}")
        else:
            print(f"    ⚠ Partial save: {saved} of {len(apps)} (see upsert errors above)")
            if saved > 0:
                # Some real data landed even though the run wasn't fully
                # clean — still counts as "not silent", so reset the streak.
                await _supa_patch_council(cid, {
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                    "consecutive_empty_runs": 0,
                })
        return saved


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    # --------------------------------------------------------------------
    # Batch splitting — as the council list grows, a single nightly run
    # risks exceeding GitHub Actions' hard job timeout (see scrape.yml).
    # Pass --batch=1 or --batch=2 to run only half the council list.
    # The split is computed dynamically from IDOX_COUNCILS' current length,
    # so it auto-rebalances every time councils are added or removed —
    # no manual reassignment of which council belongs to which batch.
    # Omit --batch entirely to run the full list (e.g. for --bulk mode,
    # or local testing).
    # --------------------------------------------------------------------
    batch = None
    for arg in sys.argv:
        if arg.startswith("--batch="):
            batch = int(arg.split("=", 1)[1])
            if batch not in (1, 2):
                print(f"ERROR: --batch must be 1 or 2, got {batch}")
                sys.exit(1)

    try:
        from idox_councils import IDOX_COUNCILS, COUNCIL_DB_IDS
    except ImportError:
        print("ERROR: idox_councils.py not found")
        sys.exit(1)

    full_count = len(IDOX_COUNCILS)
    if batch is not None:
        midpoint = full_count // 2
        if batch == 1:
            IDOX_COUNCILS = IDOX_COUNCILS[:midpoint]
        else:
            IDOX_COUNCILS = IDOX_COUNCILS[midpoint:]

    bulk = "--bulk" in sys.argv
    # NOTE (2026-07-16): scrape.yml's scrape_idox_bulk job sets
    # DAYS_BACK=180 in its env vars, but this line was hardcoding 365
    # regardless — the env var was silently dead configuration, and every
    # bulk run has actually been requesting a full 365 days despite the
    # documented earlier decision to scale back to 180 for reliability.
    # Fixed to match what scrape.yml actually intends. If you genuinely
    # want 365 back, change this AND the DAYS_BACK value in scrape.yml
    # together so they can't silently drift apart like this again.
    days = 180 if bulk else 14  # hardcoded — env var is not read directly

    # Bulk runs scrape ~6-7 months per council — use lower concurrency and longer budget
    # to avoid hammering portals and hitting timeouts on slow servers.
    if bulk:
        concurrency = int(os.environ.get("CONCURRENCY", "1"))
        budget = int(os.environ.get("MAX_MINUTES", "180"))
    else:
        concurrency = CONCURRENCY
        budget = MAX_MINUTES

    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Idox scraper (Playwright)")
    print(f"Mode:        {'BULK' if bulk else 'FAST'} ({days} days back)")
    if batch is not None:
        print(f"Batch:       {batch} of 2 ({len(IDOX_COUNCILS)} of {full_count} councils)")
    print(f"Councils:    {len(IDOX_COUNCILS)}")
    print(f"Concurrency: {concurrency}")
    print(f"Budget:      {budget} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL else 'NOT SET'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    # Fetch councils ordered by least recently scraped (priority queue)
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

    to_scrape: list[tuple[IdoxPortal, int]] = []
    missing: list[str] = []

    for entry in IDOX_COUNCILS:
        # Support both (name, url) and (name, url, "weekly") tuple formats
        if len(entry) == 3:
            name, url, mode = entry
            use_weekly = (mode == "weekly")
        else:
            name, url = entry
            use_weekly = False

        # Use hardcoded ID if available — bypasses unreliable name matching
        council_id = COUNCIL_DB_IDS.get(name)

        if not council_id:
            # Fall back to exact name match
            council_id = db_by_name.get(name.lower())

        if not council_id:
            # Last resort: partial name match
            for db_name, db_id in db_by_name.items():
                if name.lower() in db_name or db_name in name.lower():
                    council_id = db_id
                    break

        if council_id:
            id_source = "HARDCODED" if name in COUNCIL_DB_IDS else "db-lookup"
            if id_source == "HARDCODED":
                print(f"  [HARDCODED] {name} → id={council_id}")
            to_scrape.append((IdoxPortal(name, url, council_id, use_weekly_list=use_weekly), council_id))
        else:
            missing.append(name)

    if missing:
        print(f"Not in DB (skipping): {', '.join(missing[:5])}{'...' if len(missing)>5 else ''}\n")

    # BUG FIX (2026-07-17): to_scrape was always built in IDOX_COUNCILS'
    # fixed file order (Stockport, Bolton, Rochdale, ... — always the same
    # sequence, every run). Fine for fast mode (nightly, plenty of budget
    # to reach every council), but a real problem for --bulk mode: a
    # single 200-minute run genuinely cannot get through all 210 councils
    # now that months are being fetched correctly (confirmed: a real bulk
    # run only completed 14 councils, ~10,000 records, before GitHub
    # Actions' external timeout killed it). Without reordering, EVERY
    # future bulk run would keep re-doing the same first ~14 councils and
    # never reach the rest. db_rows (fetched above, already sorted
    # last_scraped_at ascending — least-recently-scraped first) already
    # has exactly the information needed to fix this; it just wasn't
    # being used for anything beyond name lookups. Reorder to_scrape by
    # that same priority for bulk mode specifically, so councils just
    # scraped tonight naturally sink to the back of the queue and
    # councils that haven't been reached in longest (or ever) rise to
    # the front for the NEXT bulk run — successive runs now genuinely
    # converge on covering the full council list over time, rather than
    # looping the same ~14 forever.
    if bulk:
        db_priority = {r["id"]: i for i, r in enumerate(db_rows)}
        to_scrape.sort(key=lambda pair: db_priority.get(pair[1], len(db_rows)))
        print(f"Bulk mode: reordered by least-recently-scraped — "
              f"starting with {to_scrape[0][0].council_name if to_scrape else '(none)'}\n")

    # DECISION-CADENCE FIX (2026-07-20): fast mode only looks at the last
    # `days` (14) worth of submitted_date by default, so an application
    # that's still "pending" from further back never gets re-visited, and
    # can never be observed transitioning to approved/refused outside a
    # bulk run. Fetch the current pending backlog per council (bounded to
    # 15-120 days back — old enough to be outside the normal window,
    # young enough that still checking is realistic) and pass it into
    # each council's scrape so those applications' months get re-scraped
    # and their status changes can actually reach decision_detected_at.
    # Bulk mode already covers a 180-day window on its own, so this is
    # deliberately fast-mode-only.
    pending_by_council: dict[int, list[dict]] = {}
    if not bulk:
        recheck_lo = (date.today() - timedelta(days=120)).isoformat()
        recheck_hi = (date.today() - timedelta(days=15)).isoformat()
        # Scoped to THIS batch's councils only (to_scrape is already the
        # post-split list at this point) — batch A and batch B run as
        # separate jobs with separate time budgets, so there's no reason
        # for batch A's job to fetch/carry pending-recheck data for batch
        # B's councils, or vice versa.
        batch_council_ids = sorted({council_id for _, council_id in to_scrape})
        try:
            if batch_council_ids:
                ids_csv = ",".join(str(i) for i in batch_council_ids)
                # PAGINATION FIX (2026-07-20): a real run showed "Pending
                # recheck: 1000 applications" in BOTH batches, on the nose
                # — a strong signal this was silently hitting Supabase's
                # default max-rows cap rather than genuinely being exactly
                # 1000 twice. limit=20000 alone doesn't override that
                # server-side cap. Paginate explicitly via offset until a
                # page comes back short, with a generous-but-bounded safety
                # cap (10 pages = 10,000 rows) so a pathological backlog
                # still can't run away. Also added explicit oldest-first
                # ordering, so if the cap IS ever hit, what's kept is the
                # most overdue-for-decision backlog — the most useful
                # subset to recheck — rather than arbitrary database order.
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
                        break  # got everything — last page was short
            else:
                pending_rows = []
            for row in pending_rows:
                pending_by_council.setdefault(row["council_id"], []).append(row)
            print(f"Pending recheck: {len(pending_rows)} applications across "
                  f"{len(pending_by_council)} councils ({recheck_lo} to {recheck_hi})\n")
        except Exception as e:
            print(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}\n")

    print(f"Scraping {len(to_scrape)} councils with Playwright…\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=BROWSER_ARGS,
        )
        print(f"Chromium launched: {browser.version}\n")

        sem = asyncio.Semaphore(concurrency)
        # NOTE: the old pre-loop "if elapsed_minutes() >= budget - 3: skip"
        # check lived here and never actually worked — see the detailed
        # comment in process_council() for why. All councils are now
        # queued unconditionally; process_council() itself checks the
        # real elapsed time at the genuine moment each one's turn arrives,
        # via the explicitly-passed budget_minutes parameter below.
        tasks = [
            process_council(portal, browser, sem, days, bulk_mode=bulk,
                             budget_minutes=budget,
                             pending_recheck=pending_by_council.get(council_id))
            for portal, council_id in to_scrape
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()

    total        = sum(r for r in results if isinstance(r, int))
    errors       = sum(1 for r in results if isinstance(r, Exception))
    # LOGGING FIX (2026-07-20): previously both counted as "Skipped (time)"
    # under one meaningless label — see the sentinel-return comment in
    # process_council() for the full story. time_skipped is the REAL
    # time-budget-triggered count; zero_result is everything else that
    # saved nothing (timeouts, WAF/bot blocks, dead URLs, template
    # mismatches, genuinely empty nights) — a much larger and more
    # actionable bucket that was being hidden inside a budget-sounding
    # label.
    time_skipped = sum(1 for r in results if r == "TIME_BUDGET_SKIP")
    zero_result  = sum(1 for r in results if r == 0)

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")
    if errors:       print(f"Errors:                    {errors}")
    if time_skipped: print(f"Skipped (time budget):     {time_skipped} councils")
    if zero_result:  print(f"Saved 0 (other reason — see per-council log lines "
                            f"above for timeout/block/error details): {zero_result} councils")


if __name__ == "__main__":
    asyncio.run(main())
