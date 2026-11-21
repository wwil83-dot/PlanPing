#!/usr/bin/env python3
"""
PlanFind — planning-register platform family scraper (2026-09-11).

Covers councils confirmed via fylde_cluster_recon.py to run the exact
same underlying platform as Fylde Council — see
planning_register_councils.py for the full real-evidence trail behind
each entry in PLANNING_REGISTER_COUNCILS.

REAL, CONFIRMED SEARCH FLOW (differs from Fylde's own in one genuine
way): both councils need the SearchPlanning checkbox explicitly ticked
before submission succeeds — Fylde's own form doesn't require this.
Confirmed via direct testing: without ticking it, submission times
out; with it ticked, a real results page loads.

Vale of Glamorgan additionally has a disclaimer gate (Worcester does
not) — confirmed real button text "Accept & Continue", a partial-text
match, not an exact "Accept"/"Agree".

HONEST LIMITATION: unlike fylde_scraper.py, this does NOT split
results into Planning vs Building Control via a confirmed href prefix
— that specific row-level structure was never directly confirmed for
these two councils (the recon only checked page-level fingerprints:
table class, pagination). Every application found is currently treated
as a Planning application, matching what was actually searched
(SearchPlanning ticked; other categories not ticked). If a future run
reveals results containing other categories mixed in, the
ROW STRUCTURE DIAGNOSTIC below will surface it rather than silently
mis-tagging them.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from planning_register_councils import PLANNING_REGISTER_COUNCILS, COUNCIL_DB_IDS

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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
DAILY_DAYS   = int(os.environ.get("DAILY_DAYS", "14"))   # days searched one day at a time (exact submitted_date)
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "50"))

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


_STATUS_DIAGNOSED: dict[str, set[str]] = {}


def _normalise_status(s: str, council_name: str) -> str:
    """Reused logic from fylde_scraper.py's proven status vocabulary —
    same confirmed platform, very likely the same real status text,
    but diagnosed per-council in case it differs.

    HONEST LIMITATION (confirmed via first real production run,
    2026-09-11): "Application DETERMINED" (seen on Vale of Glamorgan)
    genuinely means a decision HAS been made — unlike "Invalid"/
    "Application VALID"/"INVALID - Fee Only", which are about
    pre-validation status, not decision outcome, and for which
    'pending' is a fair default. "Determined" alone doesn't reveal
    WHICH outcome (approved/refused), the same category of gap already
    accepted for edinburgh_scraper.py's "Not Development"/"Permission
    is not required" statuses. Explicitly recognized here (rather than
    left to fall through as "unrecognised") so the diagnostic doesn't
    keep re-flagging a status we've now identified — the real
    limitation (outcome unknown) remains documented here instead of
    re-discovered every run.
    """
    if not s:
        return "pending"
    key = s.lower()
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in ("consideration", "received", "pending", "awaiting")):
        return "pending"
    # Known validation-stage statuses (not decision outcomes) — 'pending'
    # is a fair, deliberate default for these.
    if any(x in key for x in ("valid", "invalid")):
        return "pending"
    # Known but genuinely ambiguous — "determined" confirms a decision
    # exists without revealing which outcome. Filed as 'pending' as the
    # least-wrong default until a detail-page visit can resolve it.
    if "determined" in key:
        return "pending"

    diagnosed = _STATUS_DIAGNOSED.setdefault(council_name, set())
    if key not in diagnosed:
        diagnosed.add(key)
        print(f"    [{council_name}] ⚠ STATUS DIAGNOSTIC: unrecognised status "
              f"{s!r} — filed as 'pending'")
    return "pending"


_ROW_STRUCTURE_DIAGNOSED: set[str] = set()


def _parse_results_page(html: str, base_url: str, council_name: str) -> list[dict]:
    """Real, confirmed structure: table class="tblResults" — same as
    Fylde's own. HONEST LIMITATION (see module docstring): the href
    prefix distinguishing Planning from other categories was NOT
    directly confirmed for these councils, unlike Fylde's own
    /Planning/Display/ split. Every row found is parsed generically
    and tagged "Planning" (matching what was actually searched), with
    a one-time diagnostic if the row shape looks unexpected."""
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    for table in soup.find_all("table", class_="tblResults"):
        rows = table.find_all("tr")
        for row in rows:
            link = row.find("a", href=True)
            if not link:
                continue  # header row or non-data row

            cells = row.find_all("td")
            if len(cells) < 4:
                if council_name not in _ROW_STRUCTURE_DIAGNOSED:
                    _ROW_STRUCTURE_DIAGNOSED.add(council_name)
                    row_text = row.get_text(" | ", strip=True)
                    print(f"    [{council_name}] ⚠ ROW STRUCTURE DIAGNOSTIC: a row had "
                          f"only {len(cells)} <td> cells (expected >= 4, matching "
                          f"Fylde's own confirmed shape). Raw row text: {row_text!r}")
                continue

            reference = link.get_text(strip=True)
            if not reference or len(reference) < 3:
                continue

            location_raw = cells[1].get_text(strip=True)
            proposal = cells[2].get_text(strip=True)
            status_raw = cells[3].get_text(strip=True)

            postcode = _extract_postcode(location_raw)
            detail_url = urljoin(base_url, link["href"])

            apps.append({
                "reference": reference,
                "address": location_raw,
                "postcode": postcode,
                "description": proposal,
                "application_type": "Planning",
                "status": _normalise_status(status_raw, council_name),
                "council_url": detail_url,
            })

    seen_refs: set[str] = set()
    deduped = []
    for a in apps:
        if a["reference"] not in seen_refs:
            seen_refs.add(a["reference"])
            deduped.append(a)
    return deduped


def _parse_welhat_results_page(html: str, base_url: str, council_name: str) -> list[dict]:
    """CONFIRMED real structure via welhat_results_diagnostic.py
    (2026-09-16): NOT a table at all, unlike Worcester/Vale of
    Glamorgan — <ul class="list" id="results"> containing
    <li class="search-results-item"> elements, each with labeled text
    blocks (Location:, Proposal:, Decision:, Decision Date:) rather
    than table columns. A genuinely different results template from
    the rest of this platform family, despite sharing the same
    underlying search-form — confirms this vendor allows per-council
    results-page themes. Real confirmed reference format seen:
    "6/2026/1334/TPO" — different from Worcester/Vale of Glamorgan's
    own reference formats too."""
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    results_list = soup.find("ul", id="results") or soup.find("ul", class_="list")
    if not results_list:
        if council_name not in _ROW_STRUCTURE_DIAGNOSED:
            _ROW_STRUCTURE_DIAGNOSED.add(council_name)
            print(f"    [{council_name}] ⚠ ROW STRUCTURE DIAGNOSTIC: real "
                  f"<ul id='results'> not found — real structure may have "
                  f"changed since confirmation")
        return apps

    items = results_list.find_all("li", class_="search-results-item")
    for item in items:
        link = item.find("a", href=True)
        reference = link.get_text(strip=True) if link else ""
        if not reference or len(reference) < 3:
            continue

        # REAL FIX (2026-09-16) — confirmed via direct HTML inspection:
        # each field is <p><strong><label>Location</label>:</strong>
        # VALUE</p> — label text and the actual value are SEPARATE text
        # nodes, so a flat get_text("\n")-then-line-match approach (the
        # original attempt) split "Location" and ":" onto their own
        # lines, meaning the "Location:" prefix check never matched
        # anything — explaining why the ENTIRE field came back empty,
        # not just the postcode. This finds each real <p>, reads its
        # <label> for the field name, and takes the <p>'s own full text
        # minus that label/colon prefix as the real value.
        location, proposal, decision = "", "", ""
        for p in item.find_all("p"):
            label = p.find("label")
            if not label:
                continue
            field_name = label.get_text(strip=True)
            full_text = p.get_text(" ", strip=True)
            value = full_text
            if value.startswith(field_name):
                value = value[len(field_name):].lstrip(": ").strip()

            if field_name == "Location":
                location = value
            elif field_name == "Proposal":
                proposal = value
            elif field_name == "Decision":
                decision = value

        postcode = _extract_postcode(location)
        detail_url = urljoin(base_url, link["href"]) if link else None

        apps.append({
            "reference": reference,
            "address": location,
            "postcode": postcode,
            "description": proposal,
            "application_type": "Planning",
            "status": _normalise_status(decision, council_name),
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


class PlanningRegisterPortal:
    def __init__(self, council_name: str, base_url: str, needs_disclaimer: bool,
                 db_council_id: int, needs_cookie_dismiss: bool = False,
                 use_iso_dates: bool = False, uses_list_results: bool = False):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        self.search_url = f"{self.base_url}/Search/Advanced"
        self.needs_disclaimer = needs_disclaimer
        self.db_council_id = db_council_id
        # ADDED for Welwyn Hatfield (2026-09-13) — two genuine platform
        # quirks confirmed via fylde4_diagnostic.py, both defaulting to
        # False so Worcester/Vale of Glamorgan's existing, confirmed
        # production behaviour is completely unchanged.
        self.needs_cookie_dismiss = needs_cookie_dismiss
        self.use_iso_dates = use_iso_dates
        # ADDED for Welwyn Hatfield (2026-09-16) — confirmed via
        # welhat_results_diagnostic.py: this council's real results
        # page is a <ul id="results"><li> list, not a tblResults table
        # like Worcester/Vale of Glamorgan. Defaults to False so their
        # confirmed-working table parsing is completely unaffected.
        self.uses_list_results = uses_list_results

    def _log(self, msg: str) -> None:
        print(f"    [{self.council_name}] {msg}")

    async def _click_disclaimer_if_present(self, page) -> None:
        """Real, confirmed via recon: Vale of Glamorgan's disclaimer
        button says "Accept & Continue" — a partial match, not an
        exact "Accept"/"Agree". Worcester has no disclaimer gate at
        all. Best-effort across several real candidate selectors."""
        if "Disclaimer" not in page.url and await page.locator("form[action*='Disclaimer/Accept']").count() == 0:
            return
        for selector in [
            "form[action*='Disclaimer/Accept'] input[type='submit']",     # Cambridgeshire County: single button, value "Planning Register"
            "input[value*='Accept' i]", "button:has-text('Accept')",
            "input[value='Agree']", "button:has-text('Agree')",
        ]:
            try:
                loc = page.locator(selector)
                if await loc.count() > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                        await loc.first.click(timeout=5_000)
                    return
            except Exception:
                continue
        self._log("⚠ Could not click through disclaimer with any known selector")

    async def _dismiss_cookie_banner_if_needed(self, page) -> None:
        """ADDED for Welwyn Hatfield (2026-09-13) — confirmed real,
        specific need via fylde4_diagnostic.py's actual error: a cookie
        consent banner overlay physically intercepts pointer events on
        the real submit button ("<div id='ccc-overlay'>...intercepts
        pointer events"). The first council in this platform family
        needing this. Best-effort across several real candidate
        selectors, confirmed working via "button:has-text('Accept')"."""
        if not self.needs_cookie_dismiss:
            return
        for sel in [
            "#ccc-dismiss-button", "#ccc-notify-accept", "button:has-text('Accept')",
            "button:has-text('I accept')", "button:has-text('OK')",
            "#ccc button", ".ccc-accept-button",
        ]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click(timeout=3_000)
                    self._log(f"Cookie consent banner dismissed via: {sel!r}")
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue
        if not getattr(self, "_cookie_warned", False):
            self._cookie_warned = True
            self._log("(no cookie banner button found - fine if none appeared; not repeated)")

    async def _kill_cookie_overlay(self, page) -> None:
        """Welwyn Hatfield: after dismissing, #ccc-overlay can still swallow clicks. Remove it outright."""
        try:
            await page.evaluate("() => { for (const sel of ['#ccc-overlay', '#ccc']) "
                                "document.querySelectorAll(sel).forEach(e => e.remove()); }")
        except Exception:
            pass

    def _fmt(self, d: date) -> str:
        return d.isoformat() if self.use_iso_dates else d.strftime("%d/%m/%Y")

    async def _search_window(self, page, d_from: date, d_to: date, exact_day: bool):
        """One Received-date search (d_from..d_to), all result pages. Returns None if the search itself failed.
        exact_day=True (d_from == d_to): every result was received that day, so it is stored as submitted_date
        (the results list shows no dates at all, which is why these councils had none before)."""
        start_str, end_str = self._fmt(d_from), self._fmt(d_to)
        try:
            await page.goto(self.search_url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await self._dismiss_cookie_banner_if_needed(page)
            if self.needs_disclaimer:
                await self._click_disclaimer_if_present(page)
                if self.search_url not in page.url:
                    await page.goto(self.search_url, wait_until="domcontentloaded", timeout=45_000)

            await self._kill_cookie_overlay(page)
            try:
                await page.wait_for_selector("#DateReceivedFrom", timeout=12_000)
            except PlaywrightTimeout:
                # form missing (blank page / disclaimer on same URL): reload once and go through the gates again
                await asyncio.sleep(3)
                await page.goto(self.search_url, wait_until="domcontentloaded", timeout=45_000)
                await self._dismiss_cookie_banner_if_needed(page)
                await self._click_disclaimer_if_present(page)
                if self.search_url not in page.url:
                    await page.goto(self.search_url, wait_until="domcontentloaded", timeout=45_000)
                await self._kill_cookie_overlay(page)
                try:
                    await page.wait_for_selector("#DateReceivedFrom", timeout=12_000)
                except PlaywrightTimeout:
                    try:
                        txt = (await page.inner_text("body"))[:300].replace("\n", " ")
                        self._log(f"PAGE TEXT when form missing ({page.url}): {txt}")
                    except Exception:
                        pass
            if await page.locator("#DateReceivedFrom").count() == 0:
                try:
                    fields = await page.evaluate("() => [...document.querySelectorAll('input,select')].map(e => e.type + '#' + e.id + '[' + e.name + ']').join(' | ')")
                    self._log(f"FORM DIAGNOSTIC (no #DateReceivedFrom) at {page.url}: {fields[:1500]}")
                except Exception:
                    pass
            await page.fill("#DateReceivedFrom", start_str, timeout=5_000)
            await page.fill("#DateReceivedTo", end_str, timeout=5_000)

            planning_checkbox = page.locator("input[name='SearchPlanning'][type='checkbox']")
            if await planning_checkbox.count() > 0:
                if not await planning_checkbox.first.is_checked():
                    try:
                        await planning_checkbox.first.check(timeout=5_000)
                    except Exception:
                        # direct JS fallback (Welwyn Hatfield: a real click fails even with force)
                        await planning_checkbox.first.evaluate(
                            "el => { el.checked = true; "
                            "el.dispatchEvent(new Event('change', {bubbles: true})); }"
                        )

            form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
            search_scope = form_loc if await form_loc.count() > 0 else page

            clicked = False
            for sel in ["button:has-text('Search'):visible", "input[type='submit']:visible",
                        "button[type='submit']:visible"]:
                loc = search_scope.locator(sel)
                if await loc.count() > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                        await loc.last.click(timeout=5_000, force=True)
                    clicked = True
                    break
            if not clicked:
                self._log("⚠ Could not find/click a real submit button")
                return None
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            self._log(f"⚠ Search {start_str}..{end_str} failed: {type(e).__name__}: {e!r}")
            return None

        parser = _parse_welhat_results_page if self.uses_list_results else _parse_results_page
        out: list = []
        seen: set = set()

        def take(apps: list) -> int:
            n = 0
            for a in apps:
                if a["reference"] not in seen:
                    seen.add(a["reference"])
                    if exact_day:
                        a["submitted_date"] = d_from.isoformat()
                    out.append(a)
                    n += 1
            return n

        take(parser(await page.content(), self.base_url, self.council_name))
        page_num = 2
        while page_num <= MAX_PAGES:
            if should_stop():
                break
            if self.uses_list_results:
                next_link = page.locator("a:has-text('Next'):visible")
            else:
                next_link = page.locator("a[aria-label='Next Page.']:visible")
            if await next_link.count() == 0:
                break
            try:
                await next_link.first.click(timeout=10_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1.0)
            except Exception as e:
                self._log(f"⚠ Could not click Next at page {page_num}: {type(e).__name__}")
                break
            page_apps = parser(await page.content(), self.base_url, self.council_name)
            if not page_apps or take(page_apps) == 0:
                break
            page_num += 1
        return out

    async def scrape(self, browser) -> list:
        """Daily Received-date searches for the last DAILY_DAYS days (newest first) so each application gets its real
        submitted_date; then 8-day windows back to DAYS_BACK only to refresh the status of older, still-open applications
        (no submitted_date from those: an unknown date must never overwrite a known one)."""
        today = date.today()
        windows: list = []
        for i in range(0, min(DAILY_DAYS, DAYS_BACK) + 1):
            d = today - timedelta(days=i)
            windows.append((d, d, True))
        lo = DAILY_DAYS + 1
        while lo <= DAYS_BACK:
            hi = min(lo + 7, DAYS_BACK)
            windows.append((today - timedelta(days=hi), today - timedelta(days=lo), False))
            lo = hi + 1

        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()
        all_apps: list = []
        by_ref: dict = {}
        failed = 0
        done = 0
        try:
            for d_from, d_to, exact in windows:
                if should_stop():
                    self._log(f"⚠ Time budget reached after {done} of {len(windows)} searches, stopping")
                    break
                apps = await self._search_window(page, d_from, d_to, exact)
                if apps is None:
                    failed += 1
                    if failed >= 3 and not all_apps:
                        self._log("⚠ Three searches failed and nothing found yet - giving up on this council")
                        break
                    continue
                done += 1
                new = 0
                for a in apps:
                    prev = by_ref.get(a["reference"])
                    if prev is None:
                        by_ref[a["reference"]] = a
                        all_apps.append(a)
                        new += 1
                    elif a.get("submitted_date") and not prev.get("submitted_date"):
                        prev["submitted_date"] = a["submitted_date"]
                if apps:
                    self._log(f"{d_from}..{d_to}: {len(apps)} found, {new} new (total {len(all_apps)})")
        finally:
            await context.close()
        self._log(f"{done} of {len(windows)} searches done ({failed} failed); with submitted date: "
                  f"{sum(1 for a in all_apps if a.get('submitted_date'))} of {len(all_apps)}")
        return all_apps


async def process_council(portal: PlanningRegisterPortal, browser) -> int:
    cid = portal.db_council_id
    print(f"\n[{portal.council_name}] (council_id={cid})")

    try:
        raw_apps = await portal.scrape(browser)
    except Exception as e:
        print(f"    [{portal.council_name}] ✗ Error: {e}")
        return 0

    if not raw_apps:
        await _supa_patch_council(cid, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        return 0

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        print(f"    [{portal.council_name}] Geocoding {len(postcodes)} postcodes…")

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
            "council_id": cid,
            "reference": a["reference"],
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "planning_register_scraper",
            **({"submitted_date": a["submitted_date"]} if a.get("submitted_date") else {}),
        })

    if fallback_count:
        print(f"    [{portal.council_name}] Council centroid fallback for {fallback_count} apps")

    print(f"    [{portal.council_name}] Upserting {len(records)} records with council_id={cid}")
    # PostgREST wants identical keys within one batch: dated and undated rows go separately
    dated = [r for r in records if "submitted_date" in r]
    undated = [r for r in records if "submitted_date" not in r]
    print(f"    [{portal.council_name}] with submitted date: {len(dated)}; without (older status refresh): {len(undated)}")
    ok = True
    for batch in (dated, undated):
        if batch:
            ok = bool(await _supa_upsert(batch)) and ok
    if ok:
        print(f"    [{portal.council_name}] ✓ Saved {len(records)}")
        await _supa_patch_council(cid, {
            "coverage_source": "planning_register_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })
    return len(records) if ok else 0


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind planning-register platform scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder (None) "
              "DB id in planning_register_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun planning_register_councils.py's INSERT_SQL in Supabase "
              "first, then replace each None above with the real id.")
        sys.exit(1)

    portals = [
        PlanningRegisterPortal(
            name, base_url, needs_disclaimer, COUNCIL_DB_IDS[name],
            needs_cookie_dismiss=needs_cookie_dismiss, use_iso_dates=use_iso_dates,
            uses_list_results=uses_list_results,
        )
        for name, base_url, needs_disclaimer, needs_cookie_dismiss, use_iso_dates, uses_list_results
        in PLANNING_REGISTER_COUNCILS
    ]

    print(f"Scraping {len(portals)} councils…\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        total = 0
        for portal in portals:
            total += await process_council(portal, browser)

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")


if __name__ == "__main__":
    asyncio.run(main())