#!/usr/bin/env python3
"""
PlanFind — 'getApplications' platform family scraper.

Built 2026-08-17 from real, live evidence gathered directly through a
user's browser DevTools session — real Network tab captures, real
Console fetch() tests, not assumed. Covers 4 councils on one shared
platform: Liverpool, Warrington, Newcastle, Blackburn with Darwen. See
getapplications_councils.py for the council list and confirmed base
URLs.

ARCHITECTURE — CONFIRMED, real evidence trail:
  - The weekly-received-applications list is reached via a real POST to
    "{base_url}/planning/index.html", body
    "fa=getReceivedWeeklyList&week=DD-MM-YYYY" (week = the Monday of
    the target week — CONFIRMED via a real Console fetch() test that
    returned 222KB of real, current HTML with no CAPTCHA and no
    session/auth required at all).
  - Individual application detail pages are reached via
    "{base_url}/planning/index.html?fa=getApplication&id=N" — CONFIRMED
    real via recon, contains real fields including applicant/agent name
    and a distinct real Decision field (unlike NI's platform, this one
    DOES expose the actual approve/refuse outcome, not just "a decision
    happened").
  - CONFIRMED (2026-08-17, real evidence): a real Chromium browser
    (Playwright) run from GitHub's standard ubuntu-latest hosted runner
    got an IDENTICAL WAF block ("Error (IDX002)", same exact byte
    length) as a plain httpx request — ruling out a browser-fingerprint
    block. The SAME run from the self-hosted UK runner succeeded
    completely, real content, every council. This is an IP/datacenter-
    range block, not a bot-detection block — meaning a plain httpx
    client (no browser needed) works fine, AS LONG AS IT RUNS FROM THE
    UK RUNNER. Every job in scrape.yml for this scraper MUST use
    [self-hosted, uk-runner], never ubuntu-latest, or it will get
    blocked identically to the first two recon attempts.

HONEST LIMITATIONS in this v1, worth remembering:
  - CAPTCHA CONFIRMED on the separate "Determined" weekly list
    (fa=getDeterminedWeeklyList) — real evidence: a Console fetch()
    test against it returned a captcha-main-container in the response,
    while the SAME test against getReceivedWeeklyList came back
    completely clean. This is a genuine, deliberate platform
    restriction, not something worth trying to automate past (same
    call already made for Preston's CAPTCHA in the bespoke-scrapers-
    needed list). Consequence: this scraper CANNOT get decided-outcome
    data from the Determined list at all.
  - Decided-outcome data instead comes from a pending-recheck pass —
    same architecture as idox_scraper.py's pending_recheck, adapted for
    this platform's different shape. Every application is saved with
    council_url pointing at its real individual detail page (the URL
    itself encodes the platform's internal numeric id, which our own
    schema has no separate column for — reusing council_url avoids
    needing a schema change). A bounded batch of previously-pending
    applications gets their detail page re-fetched each run and their
    real Decision field checked directly.
  - NO CONFIRMED PAGINATION on the weekly list — recon found no
    pagination links via keyword search, and a direct user check found
    no visible "next page" control either. A week with more
    applications than fit on one response could be silently
    incomplete. Worth watching (a per-run count that suspiciously caps
    at a round number, e.g. always exactly 50, would be the tell) but
    not confirmed to actually happen yet.
  - The weekly list's real table markup was NEVER directly inspected
    (recon's regex-based link search found the real detail links, but
    no human confirmed the exact table/column HTML structure). This
    scraper's row parser is built defensively — finds every real
    fa=getApplication&id=N link, then infers each field's value from
    the surrounding table row using the header row's real text (mapped
    by keyword, not fixed column position, so it stays correct even if
    column order differs slightly between the 4 councils). A real
    diagnostic prints the actual header/first-row text ONCE per run if
    parsing produces suspiciously empty results, so a genuine mismatch
    surfaces immediately rather than silently returning nothing forever
    — same discipline as every RESULTS CONTAINER/FIELD diagnostic
    elsewhere in this project.
  - submitted_date is set to the Monday of the week each application
    was FOUND in (real evidence — from the weekly list's own real
    "week" parameter), not necessarily the exact day it was received.
    The weekly list's real per-row structure was never confirmed to
    contain an explicit per-application date at all (see the row-parse
    limitation above) — week-level precision is what's confirmed
    available, genuinely more accurate than defaulting to today's date,
    but coarser than Idox's day-level submitted_date.
  - Application Status (e.g. "Consultation/Publicity", seen in real
    recon evidence) is a WORKFLOW STAGE, not a decision outcome —
    deliberately NOT used to set this project's own `status` field.
    Only the detail page's real `Decision` field (Approved/Refused/
    Granted/etc., confirmed to exist as a distinct field from
    Application Status) is used for that, matching how every other
    scraper in this project defines status. A newly-scraped application
    with no Decision yet defaults to "pending" — self-consistent, same
    reasoning as civica_scraper.py's documented status default.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "20"))
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "2"))
WEEKS_BACK   = int(os.environ.get("WEEKS_BACK", "2"))   # current week's Monday
                                                            # + this many previous
                                                            # Mondays
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "100"))  # bounded detail-page
                                                                # revisits per run —
                                                                # each is a separate
                                                                # real HTTP request,
                                                                # keep this modest

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mondays_back(n: int) -> list[date]:
    """Real Mondays going back n weeks INCLUSIVE of the current week's
    Monday — CONFIRMED real format needed is DD-MM-YYYY, a real Monday
    date, not just any day in the target week (unconfirmed whether a
    non-Monday date would also work; using the confirmed-safe value)."""
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    return [this_monday - timedelta(weeks=i) for i in range(n + 1)]


def _normalise_status(s: str) -> str:
    """Reused directly from idox_scraper.py's proven logic — but here
    it's applied ONLY to the detail page's real Decision field, never
    to Application Status (a workflow stage, not an outcome — see
    module docstring)."""
    if not s:
        return "pending"
    s = s.lower()
    if any(x in s for x in ("approv", "grant", "permit", "allow", "no objection")):
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


def _parse_uk_date(s: str) -> Optional[str]:
    """Detail page dates observed in real recon evidence as DD-MM-YYYY
    (e.g. '16-08-2026')."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_id_from_url(url: str) -> Optional[str]:
    try:
        qs = parse_qs(urlparse(url).query)
        ids = qs.get("id")
        return ids[0] if ids else None
    except Exception:
        return None


_ROW_PARSE_DIAGNOSED: set[str] = set()


def _diagnose_row_parse(council_name: str, header_cells: list[str], sample_row: list[str]):
    """Real evidence, once per council per run — if row parsing is
    producing suspiciously empty results, print the ACTUAL header/row
    text found so a genuine markup mismatch is immediately visible
    rather than silently returning nothing forever. Same discipline as
    idox_scraper.py's RESULTS CONTAINER/FIELD diagnostics."""
    if council_name in _ROW_PARSE_DIAGNOSED:
        return
    _ROW_PARSE_DIAGNOSED.add(council_name)
    print(f"    [{council_name}] ROW PARSE DIAGNOSTIC: real header cells found: "
          f"{header_cells!r}")
    print(f"    [{council_name}] ROW PARSE DIAGNOSTIC: real first-row cells found: "
          f"{sample_row!r}")


# Keyword map: our field name -> substrings that might appear in this
# platform's real column headers. Matched case-insensitively. Not
# confirmed exact — see module docstring's honest-limitations section.
_HEADER_KEYWORDS = {
    "reference":   ["application"],
    "address":     ["location", "address", "site"],
    "description": ["proposal", "description"],
    "ward":        ["ward"],
    "parish":      ["community", "parish"],
}


_EMPTY_RESPONSE_DIAGNOSED: set[str] = set()


def _diagnose_empty_response(council_name: str, week_str: str, html: str):
    """Real evidence, once per council per run — a week with ZERO
    fa=getApplication links found could genuinely mean no applications
    that week, or could mean we got served a different/blocked/empty
    page despite a 200 status. Print enough of the real response to
    tell the difference, rather than silently treating both cases the
    same way forever."""
    if council_name in _EMPTY_RESPONSE_DIAGNOSED:
        return
    _EMPTY_RESPONSE_DIAGNOSED.add(council_name)
    print(f"    [{council_name}] EMPTY RESPONSE DIAGNOSTIC (week {week_str}): "
          f"0 real application links found. Response length: {len(html)} chars. "
          f"First 500 chars: {html[:500]!r}")


def _parse_weekly_list(html: str, base_url: str, council_name: str,
                        week_str: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    detail_links = soup.find_all("a", href=re.compile(r"fa=getApplication&(?:amp;)?id="))
    if not detail_links:
        _diagnose_empty_response(council_name, week_str, html)
        return apps

    for link in detail_links:
        href = link.get("href", "")
        app_id = _extract_id_from_url(href.replace("&amp;", "&"))
        if not app_id:
            continue

        row = link.find_parent("tr")
        cell_texts: list[str] = []
        header_texts: list[str] = []

        if row is not None:
            cells = row.find_all("td")
            cell_texts = [c.get_text(" ", strip=True) for c in cells]

            table = row.find_parent("table")
            if table is not None:
                header_row = table.find("tr")
                if header_row is not None:
                    header_cells = header_row.find_all(["th", "td"])
                    header_texts = [h.get_text(" ", strip=True) for h in header_cells]

        # Build a field dict via keyword-matched header position when
        # possible; fall back to positional guessing only as a last
        # resort, and always keep the raw cell texts too so nothing is
        # silently lost if the mapping is wrong.
        field = {"id": app_id, "council_url": urljoin(base_url, href.replace("&amp;", "&"))}
        matched_any = False
        if header_texts and cell_texts and len(header_texts) == len(cell_texts):
            lower_headers = [h.lower() for h in header_texts]
            for our_field, keywords in _HEADER_KEYWORDS.items():
                for i, h in enumerate(lower_headers):
                    if any(k in h for k in keywords):
                        field[our_field] = cell_texts[i]
                        matched_any = True
                        break

        if not matched_any:
            _diagnose_row_parse(council_name, header_texts, cell_texts)
            # Best-effort positional fallback — real screenshots showed
            # Application | Location Details | Proposal | Ward |
            # Community as the first 5 real columns, in that order.
            if len(cell_texts) >= 5:
                field.setdefault("reference", cell_texts[0])
                field.setdefault("address", cell_texts[1])
                field.setdefault("description", cell_texts[2])
                field.setdefault("ward", cell_texts[3])
                field.setdefault("parish", cell_texts[4])

        if field.get("reference"):
            apps.append(field)

    return apps


_DETAIL_LABELS = [
    "Application Reference Number", "Application Type", "Proposal",
    "Applicant", "Location", "Grid Reference", "Ward",
    "Parish / Community", "Officer", "Decision Level",
    "Application Status", "Received Date", "Valid Date", "Expiry Date",
    "Extension Of Time", "Extension Of Time Due Date",
    "Planning Performance Agreement", "Planning Performance Agreement Due Date",
    "Proposed Committee Date", "Actual Committee Date",
    "Decision Issued Date", "Decision", "Appeal Reference",
    "Appeal Status", "Appeal External Decision", "Appeal External Decision Date",
]
# Real field labels, transcribed directly from a real detail-page
# screenshot (id=178037) — every visible label on that page, not just
# the ones this scraper actually uses. CONFIRMED BUG (2026-08-17): an
# earlier version of this list only included the ~14 labels the
# scraper cares about, which meant real, present-but-unused labels
# like "Grid Reference" and "Expiry Date" weren't recognised as valid
# stop-boundaries — their real values were getting silently swallowed
# into the PRECEDING field instead (e.g. Location ate "Grid Reference:
# 338638, 391231" as part of its own value). Caught by a direct test
# against reconstructed real screenshot data before this ever touched
# production. Every real label needs to be listed here even if unused,
# purely so the regex knows where each real field genuinely ends.
_LABEL_PATTERN = "|".join(re.escape(l).replace(r"\ /\ ", r"\s*/\s*") for l in _DETAIL_LABELS)
_DETAIL_LABEL_RE = re.compile(
    rf"({_LABEL_PATTERN})\s*:\s*(.*?)(?=(?:{_LABEL_PATTERN})\s*:|\Z)",
    re.DOTALL,
)


def _parse_detail_page(html: str) -> dict:
    """Real field labels confirmed directly from a real screenshot of a
    live detail page. HTML tag structure NOT confirmed (the exact
    element wrapping each label/value pair) — parses the page's visible
    text generically via a label:value regex instead of relying on
    unconfirmed CSS selectors, so it survives minor markup differences
    across the 4 councils."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    # Collapse the label:value pairs which may have line breaks between
    # the label and its value in the rendered text
    text = re.sub(r"(:)\s*\n\s*", r"\1 ", text)

    fields = {}
    for m in _DETAIL_LABEL_RE.finditer(text):
        label = m.group(1).strip()
        value = m.group(2).strip()
        fields[label] = value
    return fields


# ---------------------------------------------------------------------------
# Supabase REST API — identical to every other scraper in this project
# ---------------------------------------------------------------------------
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
                cid_hint = records[0].get("council_id") if records else "?"
                print(f"    ✗ Upsert HTTP {r.status_code} (council_id={cid_hint}): {r.text[:300]}")
                return False
            return True
    except Exception as e:
        cid_hint = records[0].get("council_id") if records else "?"
        print(f"    ✗ Upsert exception (council_id={cid_hint}): {e}")
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
            print(f"    ⚠ Failed to increment empty-run counter (council_id={council_id}): {e}")


# ---------------------------------------------------------------------------
# Geocoding — identical to every other scraper in this project
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
# GetApplicationsPortal — one per council
# ---------------------------------------------------------------------------
class GetApplicationsPortal:
    def __init__(self, council_name: str, base_url: str, db_council_id: int):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.db_council_id = db_council_id

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def scrape_weekly_lists(self, client: httpx.AsyncClient, weeks_back: int) -> list[dict]:
        all_apps: list[dict] = []
        seen_ids: set[str] = set()

        # ESTABLISH A REAL SESSION FIRST — real evidence, not a guess:
        # the first production run against all 4 councils returned 0
        # applications across every week, with no error anywhere (every
        # request got a normal-looking response). The one confirmed-
        # working test (a real DevTools Console fetch()) used
        # credentials: "same-origin", meaning it reused cookies from a
        # normal page load that happened first. This scraper's client
        # previously POSTed cold, no prior page visit, no cookies. A
        # plain GET here (using the SAME client instance, which keeps
        # cookies automatically) tests that theory directly rather than
        # guessing blind a second time.
        search_page_url = f"{self.base_url}/planning/index.html?fa=getApplications"
        try:
            await client.get(search_page_url, headers=HTTP_HEADERS, timeout=30,
                              follow_redirects=True)
        except Exception as e:
            self._log(f"⚠ Could not load search page first (continuing anyway, "
                      f"may affect results): {e}")

        for monday in _mondays_back(weeks_back):
            if should_stop():
                self._log(f"⚠ Time budget reached, stopping at week {monday}")
                break

            week_str = monday.strftime("%d-%m-%Y")
            url = f"{self.base_url}/planning/index.html"
            try:
                r = await client.post(
                    url,
                    data={"fa": "getReceivedWeeklyList", "week": week_str},
                    headers=HTTP_HEADERS,
                    timeout=30,
                    follow_redirects=True,
                )
            except Exception as e:
                self._log(f"⚠ Request error for week {week_str}: {e}")
                continue

            if r.status_code >= 400:
                self._log(f"⚠ HTTP {r.status_code} for week {week_str}: {r.text[:200]}")
                continue

            week_apps = _parse_weekly_list(r.text, self.base_url, self.council_name, week_str)
            for a in week_apps:
                a["week_monday"] = monday.isoformat()  # real evidence of WHEN this
                                                          # was received, from the
                                                          # week list itself — not
                                                          # today's date
            new_apps = [a for a in week_apps if a["id"] not in seen_ids]
            for a in new_apps:
                seen_ids.add(a["id"])
            self._log(f"Week {week_str}: {len(week_apps)} results "
                      f"({len(new_apps)} new)")
            all_apps.extend(new_apps)

        return all_apps

    async def recheck_pending(self, client: httpx.AsyncClient,
                               pending: list[dict]) -> list[dict]:
        """Revisits a bounded batch of previously-pending applications'
        real detail pages to check for a real Decision. See module
        docstring — this is the ONLY route to decided-outcome data,
        since the Determined weekly list is CAPTCHA-protected."""
        updates = []
        for p in pending:
            if should_stop():
                self._log(f"⚠ Time budget reached mid-recheck, stopping")
                break
            app_id = _extract_id_from_url(p.get("council_url", "") or "")
            if not app_id:
                continue
            url = f"{self.base_url}/planning/index.html?fa=getApplication&id={app_id}"
            try:
                r = await client.get(url, headers=HTTP_HEADERS, timeout=30,
                                      follow_redirects=True)
            except Exception:
                continue
            if r.status_code >= 400:
                continue
            fields = _parse_detail_page(r.text)
            decision = fields.get("Decision", "").strip()
            if decision:
                updates.append({
                    "reference": p["reference"],
                    "status": _normalise_status(decision),
                    "decision_date": _parse_uk_date(fields.get("Decision Issued Date", "")),
                })
        if updates:
            self._log(f"Recheck: {len(updates)} of {len(pending)} previously-pending "
                      f"application(s) now have a real decision")
        return updates


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def process_council(portal: GetApplicationsPortal, client: httpx.AsyncClient,
                           sem: asyncio.Semaphore, weeks_back: int,
                           pending_recheck: Optional[list[dict]] = None) -> int:
    async with sem:
        cid = portal.db_council_id
        print(f"\n[{portal.council_name}] (council_id={cid})")

        if should_stop():
            print(f"    [{portal.council_name}] — skipping, time budget reached "
                  f"({elapsed_minutes():.1f} min elapsed)")
            return "TIME_BUDGET_SKIP"

        try:
            raw_apps = await portal.scrape_weekly_lists(client, weeks_back)
        except Exception as e:
            print(f"    [{portal.council_name}] ✗ Error: {e}")
            return 0

        # Apply any real decision updates found via the recheck pass —
        # done regardless of whether this week's list itself had new
        # applications, since a recheck can update OLDER records too.
        recheck_updates = []
        if pending_recheck:
            try:
                recheck_updates = await portal.recheck_pending(client, pending_recheck)
            except Exception as e:
                print(f"    [{portal.council_name}] ⚠ Recheck error: {e}")

        if not raw_apps and not recheck_updates:
            await _supa_patch_council(cid, {
                "last_scraped_at": datetime.now(timezone.utc).isoformat()
            })
            await _supa_increment_empty_runs(cid)
            return 0

        apps = []
        for a in raw_apps:
            address = a.get("address", "")
            apps.append({
                "reference":        a.get("reference"),
                "address":          address,
                "postcode":         _extract_postcode(address),
                "description":      a.get("description"),
                "application_type": None,  # relies on main.py's reference-suffix
                                             # fallback, same as ni_scraper.py
                "status":           "pending",  # list view never shows outcome —
                                                  # see module docstring
                "submitted_date":   a.get("week_monday"),  # real evidence: the
                                                              # Monday of the week
                                                              # this application
                                                              # was found in. Not
                                                              # the exact day it
                                                              # was received — the
                                                              # weekly list doesn't
                                                              # give per-row dates
                                                              # (unconfirmed
                                                              # whether the real
                                                              # table even HAS one;
                                                              # see honest-
                                                              # limitations above)
                                                              # — but genuinely
                                                              # more accurate than
                                                              # defaulting to
                                                              # today's date.
                "decision_date":    None,
                "council_url":      a.get("council_url"),
            })

        seen: set[str] = set()
        unique_apps = []
        for a in apps:
            if a["reference"] and a["reference"] not in seen:
                seen.add(a["reference"])
                unique_apps.append(a)
        apps = unique_apps

        need = [a["postcode"] for a in apps if a.get("postcode")]
        if need:
            print(f"    [{portal.council_name}] Geocoding {len(set(need))} postcodes…")
            coords = await geocode(need)
            for app in apps:
                if app.get("postcode"):
                    pc = app["postcode"].strip().upper().replace(" ", "")
                    if pc in coords:
                        app["lat"], app["lng"] = coords[pc]

        geocoded = [(a.get("lat"), a.get("lng")) for a in apps if a.get("lat") and a.get("lng")]
        if geocoded:
            import statistics
            centroid_lat = statistics.median(g[0] for g in geocoded)
            centroid_lng = statistics.median(g[1] for g in geocoded)
            fallback_count = 0
            for app in apps:
                if not app.get("lat"):
                    app["lat"] = centroid_lat
                    app["lng"] = centroid_lng
                    app["geocode_quality"] = "centroid"
                    fallback_count += 1
            if fallback_count:
                print(f"    [{portal.council_name}] Council centroid fallback for {fallback_count} apps")

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
            "source":           "getapplications_scraper",
        } for a in apps]

        # Recheck updates are separate, smaller upserts — only the
        # changed fields, keyed by the same (council_id, reference)
        # conflict target so they merge into the existing row rather
        # than overwriting address/description with blanks.
        for u in recheck_updates:
            records.append({
                "council_id": cid,
                "reference":  u["reference"],
                "status":     u["status"],
                "decision_date": u["decision_date"],
                "source":     "getapplications_scraper",
            })

        if records:
            print(f"    [{portal.council_name}] Upserting {len(records)} records with council_id={cid}")

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
                "coverage_source": "getapplications_scraper",
                "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_empty_runs": 0,
                "active": True,
            })
            print(f"    [{portal.council_name}] ✓ Saved {saved}")
        else:
            print(f"    [{portal.council_name}] ⚠ Partial save: {saved} of {len(records)}")
            if saved > 0:
                await _supa_patch_council(cid, {
                    "last_saved_at": datetime.now(timezone.utc).isoformat(),
                    "consecutive_empty_runs": 0,
                })
        return saved


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind getApplications-family scraper (direct API)")
    print(f"Weeks back:  {WEEKS_BACK}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL else 'NOT SET'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    try:
        from getapplications_councils import GETAPPLICATIONS_COUNCILS, COUNCIL_DB_IDS
    except ImportError:
        print("ERROR: getapplications_councils.py not found")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder "
              "(None) DB id in getapplications_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun getapplications_councils.py's INSERT_SQL in Supabase first, "
              "then replace each None above with the real id Supabase assigns.")
        sys.exit(1)

    to_scrape = [
        GetApplicationsPortal(name, base_url, COUNCIL_DB_IDS[name])
        for name, base_url in GETAPPLICATIONS_COUNCILS
    ]
    council_ids = [p.db_council_id for p in to_scrape]

    # Fetch a bounded batch of currently-pending applications for these
    # councils to recheck — same shape as idox_scraper.py's
    # pending_recheck, adapted for this platform's per-application
    # detail-page-visit model rather than a batched date-range query.
    pending_by_council: dict[int, list[dict]] = {cid: [] for cid in council_ids}
    try:
        ids_csv = ",".join(str(i) for i in council_ids)
        pending_rows = await _supa_get(
            "planning_applications",
            select="council_id,reference,council_url",
            status="eq.pending",
            council_id=f"in.({ids_csv})",
            order="submitted_date.asc",
            limit=str(RECHECK_LIMIT),
        )
        for row in pending_rows:
            pending_by_council.setdefault(row["council_id"], []).append(row)
        print(f"Pending recheck: {len(pending_rows)} applications across "
              f"{len(to_scrape)} councils (bounded to {RECHECK_LIMIT} total)\n")
    except Exception as e:
        print(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}\n")

    print(f"Scraping {len(to_scrape)} councils via direct API "
          f"(no browser needed — MUST run on the UK runner, see module "
          f"docstring)…\n")

    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(
            *[process_council(p, client, sem, WEEKS_BACK,
                               pending_recheck=pending_by_council.get(p.db_council_id))
              for p in to_scrape],
            return_exceptions=True,
        )

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
        for r in results:
            if isinstance(r, Exception):
                print(f"  {r!r}")


if __name__ == "__main__":
    asyncio.run(main())
