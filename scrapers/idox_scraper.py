#!/usr/bin/env python3
"""
PlanFind Idox scraper — scrapes UK councils running the Idox planning portal.

Approach: uses the monthlyList endpoint (GET, no CSRF, no JS needed on classic Idox)
rather than the advanced search POST form (which requires JavaScript on Idox v5+).

For each council:
  1. GET /online-applications/search.do?action=monthlyList&searchType=Application
  2. Parse the monthlyListForm and submit (date received, current month)
  3. Follow pagination via pagedSearchResults.do
  4. Filter results by submitted_date in Python (last N days)

Portals returning "Browser does not support script" are flagged as Idox v5 
(cloud-hosted, JS-required) — these need Playwright and are tracked for future work.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse, urlencode

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "40"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "7"))
CONCURRENCY  = int(os.environ.get("CONCURRENCY", "3"))
REQ_DELAY    = 1.5  # seconds between requests to same server

START_TIME = time.monotonic()

def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60

def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
    if not s: return "pending"
    s = s.lower()
    if any(x in s for x in ("approv","grant","permit","allow","no objection")):
        return "approved"
    if any(x in s for x in ("refus","reject","dismiss","not permit")):
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
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _is_js_required(html: str) -> bool:
    """Detect Idox v5 cloud portal (PublicAccess 5) which requires JavaScript."""
    lower = html.lower()
    return "browser does not support script" in lower or "noscript" in lower and "please enable javascript" in lower


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------
def _h():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

async def _supa_get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_h())
        r.raise_for_status()
        return r.json()

async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{SUPABASE_URL}/rest/v1/planning_applications",
            json=records, headers=headers,
        )
        return r.status_code in (200, 201, 204)

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
                    json={"postcodes": unique[i:i+100]},
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


# ---------------------------------------------------------------------------
# Idox portal scraper
# ---------------------------------------------------------------------------
USER_AGENT = "PlanFind/1.0 (+https://planfind.co.uk; planning data aggregator)"


class IdoxPortal:
    """
    Scrapes one Idox planning portal using the monthlyList endpoint.
    This avoids the CSRF/JavaScript requirements of the advanced search form.
    """

    def __init__(self, council_name: str, base_url: str):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        parsed = urlparse(self.base_url)
        self.domain_root = f"{parsed.scheme}://{parsed.netloc}"

    def _abs_url(self, href: str) -> str:
        if not href: return ""
        if href.startswith("http"): return href
        if href.startswith("/"): return f"{self.domain_root}{href}"
        return f"{self.base_url}/{href.lstrip('/')}"

    async def _get_monthly_list_html(
        self, client: httpx.AsyncClient, for_date: date
    ) -> Optional[str]:
        """
        GET the monthly list page and submit the form to get results.
        Returns the results HTML, or None on failure.
        """
        # Step 1: load the monthly list form page
        try:
            r = await client.get(
                f"{self.base_url}/search.do",
                params={"action": "monthlyList", "searchType": "Application"},
                timeout=15,
            )
            await asyncio.sleep(REQ_DELAY)
        except Exception as e:
            print(f"    GET monthly list error: {e}")
            return None

        if r.status_code != 200:
            print(f"    HTTP {r.status_code} on monthly list page — skipping")
            return None

        if _is_js_required(r.text):
            print(f"    ⚠ PublicAccess 5 (JS required) — needs Playwright upgrade")
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", id="monthlyListForm") or soup.find("form")
        if not form:
            title = soup.find("title")
            print(f"    No monthly list form found — page: '{title.get_text(strip=True)[:50] if title else 'none'}'")
            return None

        # Step 2: build form data from all hidden inputs + set date received
        form_data: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            val = inp.get("value", "")
            itype = inp.get("type", "text").lower()
            if name and itype not in ("submit", "button", "image"):
                form_data[name] = val

        # Override: request by date received, for target month
        form_data["searchType"] = "Application"
        # Try both common field names for the date received radio
        for radio_name in ("dateReceived", "date(applicationReceived)", "type"):
            form_data[radio_name] = "receivedDate"

        # Set month and year (some forms have selects for this)
        form_data["month"] = str(for_date.month)
        form_data["year"] = str(for_date.year)

        # Get form action
        action = form.get("action", "search.do?action=monthlyList")
        post_url = self._abs_url(action) if action else f"{self.base_url}/search.do"

        # Step 3: submit the form
        try:
            r2 = await client.post(
                post_url,
                data=form_data,
                timeout=20,
            )
            await asyncio.sleep(REQ_DELAY)
        except Exception as e:
            print(f"    Monthly list POST error: {e}")
            return None

        if r2.status_code != 200:
            print(f"    Monthly list POST returned HTTP {r2.status_code}")
            return None

        return r2.text

    def _parse_page(self, html: str) -> tuple[list[dict], bool]:
        """Parse a results page. Returns (apps, has_next_page)."""
        soup = BeautifulSoup(html, "html.parser")
        apps = []

        # Try multiple container patterns
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
            app = self._parse_result(item)
            if app:
                apps.append(app)

        has_next = bool(
            soup.find("a", string=re.compile(r"^Next$", re.I))
            or soup.find("a", {"class": "next"})
            or soup.find("li", {"class": "next"})
        )
        return apps, has_next

    def _parse_result(self, item) -> Optional[dict]:
        """Parse one search result element."""
        link = item.find("a", href=True)
        if not link: return None

        ref = link.get_text(strip=True)
        portal_url = self._abs_url(link.get("href", ""))

        heading = item.find(re.compile(r"h[2-4]"))
        if heading:
            ref = heading.get_text(strip=True)

        if not ref or len(ref) < 3:
            return None

        address = ""
        addr_el = item.find(class_=re.compile(r"\baddress\b", re.I))
        if addr_el:
            address = addr_el.get_text(" ", strip=True)

        # Parse metadata from dl/dt/dd or p.metaInfo
        fields: dict[str, str] = {}
        meta = item.find(class_=re.compile(r"metaInfo|meta-info|metadata", re.I))
        if meta:
            text = meta.get_text(" | ", strip=True)
            # Idox metaInfo format: "Ref. No: X | Status: Y | Received: Z | Validated: W"
            for part in text.split("|"):
                part = part.strip()
                if ":" in part:
                    k, _, v = part.partition(":")
                    fields[k.strip().lower()] = v.strip()

        for dl in item.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                k = dt.get_text(strip=True).lower().rstrip(":")
                v = dd.get_text(" ", strip=True)
                fields[k] = v

        if not address:
            address = fields.get("address") or fields.get("site address") or fields.get("location") or ""

        description = (
            fields.get("proposal") or fields.get("description") or
            fields.get("development description") or ""
        )
        app_type = (
            fields.get("application type") or fields.get("type") or
            fields.get("app type") or ""
        )
        status_raw = (
            fields.get("status") or fields.get("decision") or
            fields.get("current status") or ""
        )
        date_raw = (
            fields.get("date received") or fields.get("received") or
            fields.get("date validated") or fields.get("validated") or
            fields.get("date registered") or ""
        )

        # Also try the description from the link text if not in fields
        if not description:
            description = link.get_text(strip=True)
            if description == ref:
                description = ""

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
            "council_name":     self.council_name,
            "council_url":      portal_url,
            "source":           "idox_scraper",
        }

    async def scrape(self, days_back: int = 7) -> list[dict]:
        """
        Scrape recent applications. Uses monthlyList for current month,
        and previous month if within the lookback window.
        """
        cutoff = date.today() - timedelta(days=days_back)
        months_to_scrape = [date.today()]

        # If the lookback window crosses into the previous month, scrape that too
        if cutoff.month != date.today().month or cutoff.year != date.today().year:
            months_to_scrape.append(cutoff)

        all_apps: list[dict] = []

        async with httpx.AsyncClient(
            headers={
                "User-Agent":      USER_AGENT,
                "Accept":          "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
            },
            follow_redirects=True,
            timeout=20,
            verify=False,
        ) as client:

            for target_month in months_to_scrape:
                html = await self._get_monthly_list_html(client, target_month)
                if not html:
                    continue

                page = 1
                while True:
                    apps, has_next = self._parse_page(html)
                    all_apps.extend(apps)

                    if not has_next or not apps:
                        break
                    page += 1
                    if page > 50:
                        print(f"    ⚠ Hit 50-page cap")
                        break

                    try:
                        r = await client.get(
                            f"{self.base_url}/pagedSearchResults.do",
                            params={"action": "page", "searchCriteria.page": page},
                            timeout=20,
                        )
                        html = r.text
                        await asyncio.sleep(REQ_DELAY)
                    except Exception as e:
                        print(f"    Page {page} error: {e}")
                        break

        # Filter to only applications within the lookback window
        recent = [
            a for a in all_apps
            if not a.get("submitted_date") or a["submitted_date"] >= cutoff.isoformat()
        ]

        print(f"    {len(all_apps)} this month → {len(recent)} in last {days_back} days")
        return recent


# ---------------------------------------------------------------------------
# Per-council processing
# ---------------------------------------------------------------------------
async def process_council(
    portal: IdoxPortal,
    council_id: int,
    sem: asyncio.Semaphore,
    days_back: int,
) -> int:
    async with sem:
        print(f"\n[{portal.council_name}]")
        try:
            apps = await portal.scrape(days_back)
        except Exception as e:
            print(f"    ✗ Error: {e}")
            return 0

        if not apps:
            await _supa_patch_council(council_id, {
                "last_scraped_at": datetime.now(timezone.utc).isoformat()
            })
            return 0

        need = [a["postcode"] for a in apps if not a.get("lat") and a.get("postcode")]
        if need:
            print(f"    Geocoding {len(set(need))} postcodes…")
            coords = await geocode(need)
            for app in apps:
                if not app.get("lat") and app.get("postcode"):
                    pc = app["postcode"].strip().upper().replace(" ", "")
                    if pc in coords:
                        app["lat"], app["lng"] = coords[pc]

        records = [{
            "council_id":       council_id,
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

        ok = True
        for i in range(0, len(records), 100):
            if not await _supa_upsert(records[i:i+100]):
                ok = False

        if ok:
            await _supa_patch_council(council_id, {
                "coverage_source": "idox_scraper",
                "last_scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        print(f"    ✓ Saved {len(apps)}")
        return len(apps)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    try:
        from idox_councils import IDOX_COUNCILS
    except ImportError:
        print("ERROR: idox_councils.py not found")
        sys.exit(1)

    bulk = "--bulk" in sys.argv
    days = int(os.environ.get("DAYS_BACK", "365" if bulk else "7"))

    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Idox scraper (monthlyList approach)")
    print(f"Mode:        {'BULK' if bulk else 'FAST'} ({days} days back)")
    print(f"Councils:    {len(IDOX_COUNCILS)}")
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
        print(f"Failed to fetch councils from DB: {e}")
        sys.exit(1)

    db_by_name = {r["name"].lower(): r["id"] for r in db_rows}

    to_scrape: list[tuple[IdoxPortal, int]] = []
    missing: list[str] = []

    for name, url in IDOX_COUNCILS:
        council_id = db_by_name.get(name.lower())
        if not council_id:
            for db_name, db_id in db_by_name.items():
                if name.lower() in db_name or db_name in name.lower():
                    council_id = db_id
                    break
        if council_id:
            to_scrape.append((IdoxPortal(name, url), council_id))
        else:
            missing.append(name)

    if missing:
        print(f"Not in DB (skipping): {', '.join(missing[:5])}{'...' if len(missing)>5 else ''}\n")

    print(f"Scraping {len(to_scrape)} councils…\n")

    sem = asyncio.Semaphore(CONCURRENCY)
    skipped = 0
    tasks = []

    for portal, council_id in to_scrape:
        if should_stop():
            skipped += 1
            continue
        tasks.append(process_council(portal, council_id, sem, days))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    total  = sum(r for r in results if isinstance(r, int))
    errors = sum(1 for r in results if isinstance(r, Exception))

    print(f"\n{'='*50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")
    if errors:  print(f"Errors:             {errors}")
    if skipped: print(f"Skipped (time):     {skipped} councils")


if __name__ == "__main__":
    asyncio.run(main())
