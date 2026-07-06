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
    if not s: return None
    s = str(s).strip()
    for sep in ("+", "T", " "):
        if sep in s: s = s.split(sep)[0].strip()
    s = s[:10]
    for fmt in (
        "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y",
        "%d/%m/%y", "%Y/%m/%d",
        "%d %B %Y", "%d %b %Y",
    ):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
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
    if meta:
        for part in meta.get_text(strip=True).split("|"):
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

    return {
        "reference":        ref.strip(),
        "address":          address.strip(),
        "postcode":         _extract_postcode(address),
        "lat":              None,
        "lng":              None,
        "description":      description.strip(),
        "application_type": app_type.strip(),
        "status":           _normalise_status(status_raw),
        "submitted_date":   _parse_date(date_raw),
        "decision_date":    None,
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

    async def scrape(self, browser: Browser, days_back: int = 7) -> list[dict]:
        cutoff = date.today() - timedelta(days=days_back)

        # Build the full list of calendar months to scrape.
        # Fast mode (14 days): 1-2 months. Bulk mode (365 days): up to 13 months.
        months: list[date] = []
        m = date.today().replace(day=1)
        cutoff_month = cutoff.replace(day=1)
        while m >= cutoff_month:
            months.append(m)
            if m.month == 1:
                m = m.replace(year=m.year - 1, month=12)
            else:
                m = m.replace(month=m.month - 1)

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
                # Fetch this week and last week to cover the 14-day window.
                for week_offset in range(2):  # 0 = this week, 1 = last week
                    apps = await self._scrape_week(page, week_offset)
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
            if not a.get("submitted_date") or a["submitted_date"] >= cutoff.isoformat()
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
            return []

        # — Step 2: Click "date received" radio & submit form —
        form_submitted = False

        # Explicitly select the first/current month in the dropdown
        # (some portals have no default, causing 0 results if not set)
        for month_sel in [
            "select[id='searchCriteria.monthYearIndex']",
            "select[name='searchCriteria.monthYearIndex']",
            "select[name*='monthYear']",
            "select[id*='monthYear']",
        ]:
            try:
                loc = page.locator(month_sel)
                if await loc.count() > 0:
                    await loc.select_option(index=0)
                    break
            except Exception:
                continue

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
            return []

        # — Step 3: Collect all pages —
        all_apps: list[dict] = []
        page_num = 1

        while True:
            html = await page.content()
            apps, has_next = parse_results_page(
                html, self.base_url, self.domain_root, self.council_name
            )
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

        while True:
            html = await page.content()
            apps, has_next = parse_results_page(
                html, self.base_url, self.domain_root, self.council_name
            )
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
) -> int:
    # council_id comes ONLY from the portal object — never a loose parameter
    # that could get corrupted in async concurrent execution.
    cid = portal.db_council_id

    async with sem:
        print(f"\n[{portal.council_name}] (council_id={cid})")
        await asyncio.sleep(1)  # stagger requests — avoids triggering WAF rate limits

        try:
            apps = await portal.scrape(browser, days_back)
        except Exception as e:
            print(f"    ✗ Error: {e}")
            return 0

        if not apps:
            await _supa_patch_council(cid, {
                "last_scraped_at": datetime.now(timezone.utc).isoformat()
            })
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
                "active": True,
            })
            print(f"    ✓ Saved {saved}")
        else:
            print(f"    ⚠ Partial save: {saved} of {len(apps)} (see upsert errors above)")
        return saved


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    try:
        from idox_councils import IDOX_COUNCILS, COUNCIL_DB_IDS
    except ImportError:
        print("ERROR: idox_councils.py not found")
        sys.exit(1)

    bulk = "--bulk" in sys.argv
    days = 365 if bulk else 14  # hardcoded — env var was being overridden

    # Bulk runs scrape 13 months per council — use lower concurrency and longer budget
    # to avoid hammering portals and hitting timeouts on slow servers.
    if bulk:
        concurrency = int(os.environ.get("CONCURRENCY", "1"))
        budget = int(os.environ.get("MAX_MINUTES", "180"))
    else:
        concurrency = CONCURRENCY
        budget = MAX_MINUTES

    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Idox scraper (Playwright)")
    print(f"Mode:        {'BULK' if bulk else 'FAST'} ({days} days back)")
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

    print(f"Scraping {len(to_scrape)} councils with Playwright…\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=BROWSER_ARGS,
        )
        print(f"Chromium launched: {browser.version}\n")

        sem = asyncio.Semaphore(concurrency)
        skipped = 0
        tasks = []

        for portal, council_id in to_scrape:
            if elapsed_minutes() >= budget - 3:
                skipped += 1
                continue
            tasks.append(
                process_council(portal, browser, sem, days, bulk_mode=bulk)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()

    total  = sum(r for r in results if isinstance(r, int))
    errors = sum(1 for r in results if isinstance(r, Exception))

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")
    if errors:  print(f"Errors:             {errors}")
    if skipped: print(f"Skipped (time):     {skipped} councils")


if __name__ == "__main__":
    asyncio.run(main())
