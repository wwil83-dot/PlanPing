#!/usr/bin/env python3
"""
PlanFind Idox scraper — scrapes UK councils running the Idox planning portal.

Design decisions:
  - Async with bounded concurrency (3 councils at once, each a different server)
  - Soft time budget: stops gracefully before GitHub Actions kills the job
  - Priority queue: always scrapes least-recently-updated councils first
  - Session + CSRF handled properly per council
  - Pagination follows Idox's pagedSearchResults.do pattern
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

# ---------------------------------------------------------------------------
# Config — all overridable via environment variables
# ---------------------------------------------------------------------------
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES   = int(os.environ.get("MAX_MINUTES", "40"))   # soft stop
DAYS_BACK     = int(os.environ.get("DAYS_BACK", "7"))      # 7 nightly, 365 bulk
CONCURRENCY   = int(os.environ.get("CONCURRENCY", "3"))    # parallel councils
REQ_DELAY     = 1.5  # seconds between requests to same server

START_TIME = time.monotonic()

def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60

def should_stop() -> bool:
    """True when we're 2 minutes from the budget — stop cleanly."""
    return elapsed_minutes() >= MAX_MINUTES - 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_status(s: str) -> str:
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


def _parse_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    # Strip timezone offsets and time parts
    for sep in ("+", "T", " "):
        if sep in s:
            s = s.split(sep)[0].strip()
    s = s[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Supabase REST API helpers
# ---------------------------------------------------------------------------
_H = lambda: {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

async def _supa_get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_H())
        r.raise_for_status()
        return r.json()

async def _supa_upsert(records: list) -> bool:
    headers = {**_H(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{SUPABASE_URL}/rest/v1/planning_applications",
            json=records,
            headers=headers,
        )
        return r.status_code in (200, 201, 204)

async def _supa_patch_council(council_id: int, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.patch(
            f"{SUPABASE_URL}/rest/v1/councils",
            params={"id": f"eq.{council_id}"},
            json=data,
            headers={**_H(), "Prefer": "return=minimal"},
        )


# ---------------------------------------------------------------------------
# Geocoding
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
    Handles one Idox planning portal:
      1. GET search page  → session cookies + CSRF token
      2. POST search form → first results page
      3. GET paginated    → remaining pages
      4. Parse HTML       → list of application dicts
    """

    def __init__(self, council_name: str, base_url: str):
        self.council_name = council_name
        self.base_url = base_url.rstrip("/")
        # Extract domain for building absolute URLs
        parsed = urlparse(self.base_url)
        self.domain_root = f"{parsed.scheme}://{parsed.netloc}"

    def _abs_url(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"{self.domain_root}{href}"
        return f"{self.base_url}/{href.lstrip('/')}"

    async def _init_session(self, client: httpx.AsyncClient) -> Optional[str]:
        """
        GET the search page to establish session and find CSRF token.
        Returns:
          str  — the CSRF token value (could be empty string if not needed)
          None — hard failure (can't reach the portal at all)
        """
        try:
            # Step 1: visit base URL first to establish session cookies
            await client.get(f"{self.base_url}/", timeout=10)
            await asyncio.sleep(0.5)

            # Step 2: get the advanced search page
            r = await client.get(
                f"{self.base_url}/search.do",
                params={"action": "advanced"},
                timeout=15,
            )

            if r.status_code not in (200, 302):
                print(f"    HTTP {r.status_code} on search page — skipping")
                return None

            soup = BeautifulSoup(r.text, "html.parser")

            # Method 1: Classic Idox — hidden input field
            el = soup.find("input", {"name": "_csrf"})
            if el and el.get("value"):
                return el["value"]

            # Method 2: Meta tag (some Idox versions)
            el = soup.find("meta", {"name": "_csrf"})
            if el and el.get("content"):
                return el["content"]

            # Method 3: Modern Idox v5 — cookie-based CSRF
            # Spring Security sets XSRF-TOKEN cookie; we pass it back as form field
            for cookie_name in ("XSRF-TOKEN", "CSRF-TOKEN", "_csrf", "csrftoken"):
                val = client.cookies.get(cookie_name)
                if val:
                    return val

            # Method 4: CSRF in form action URL
            form = soup.find("form")
            if form:
                action = form.get("action", "")
                m = re.search(r"[?&]_csrf=([^&]+)", action)
                if m:
                    return m.group(1)

            # Method 5: No CSRF — try submitting without it
            # Many Idox read-only searches don't enforce CSRF
            # Debug info to help diagnose
            title = soup.find("title")
            title_text = title.get_text(strip=True)[:60] if title else "no title"
            has_form = bool(form)
            print(f"    ⚠ No CSRF — page: '{title_text}' | form: {has_form} | cookies: {list(client.cookies.keys())}")
            # Return empty string = proceed without CSRF token
            return ""

        except Exception as e:
            print(f"    Session error: {e}")
            return None

    async def _post_search(
        self,
        client: httpx.AsyncClient,
        csrf: str,
        date_from: str,
        date_to: str,
    ) -> Optional[str]:
        """POST the search form; return response HTML or None."""
        form = {
            "searchType": "Application",
            "date(applicationReceived)": "",
            "dateReceivedStart": date_from,
            "dateReceivedEnd": date_to,
        }
        # Only include CSRF if we have one
        if csrf:
            form["_csrf"] = csrf

        try:
            r = await client.post(
                f"{self.base_url}/search.do",
                params={"action": "advanced"},
                data=form,
                timeout=20,
            )
            if r.status_code != 200:
                print(f"    POST returned HTTP {r.status_code}")
                return None
            # Quick check: did we get a results page or an error/redirect page?
            if "searchresults" not in r.text and "no results" not in r.text.lower():
                soup = BeautifulSoup(r.text, "html.parser")
                title = soup.find("title")
                print(f"    ⚠ Unexpected response: '{title.get_text(strip=True)[:60] if title else 'no title'}'")
            return r.text
        except Exception as e:
            print(f"    POST error: {e}")
            return None

    async def _get_page(self, client: httpx.AsyncClient, page_num: int) -> Optional[str]:
        """GET a subsequent results page."""
        try:
            r = await client.get(
                f"{self.base_url}/pagedSearchResults.do",
                params={"action": "page", "searchCriteria.page": page_num},
                timeout=20,
            )
            return r.text if r.status_code == 200 else None
        except Exception:
            return None

    def _parse_page(self, html: str) -> tuple[list[dict], bool]:
        """
        Parse a results page.
        Returns (list_of_app_dicts, has_next_page).
        """
        soup = BeautifulSoup(html, "html.parser")
        apps = []

        # Idox wraps results in <ul class="searchresults">
        container = soup.find("ul", class_="searchresults")
        if not container:
            return apps, False

        for li in container.find_all("li", class_="searchresult"):
            app = self._parse_result(li)
            if app:
                apps.append(app)

        # Next page exists if there's a "Next" pagination link
        has_next = bool(
            soup.find("a", string=re.compile(r"^Next$", re.I))
            or soup.find("a", {"class": "next"})
            or soup.find("li", {"class": "next"})
        )
        return apps, has_next

    def _parse_result(self, li) -> Optional[dict]:
        """Parse a single <li class='searchresult'> into a dict."""
        # Reference + portal URL from the anchor link
        link = li.find("a", href=True)
        if not link:
            return None

        ref = link.get_text(strip=True)
        portal_url = self._abs_url(link.get("href", ""))

        # Some Idox versions wrap the ref in a heading tag
        heading = li.find(re.compile(r"h[2-4]"))
        if heading:
            ref = heading.get_text(strip=True)

        if not ref or len(ref) < 3:
            return None

        # Address — Idox usually has <p class="address"> or <span class="address">
        address = ""
        addr_el = li.find(class_=re.compile(r"\baddress\b", re.I))
        if addr_el:
            address = addr_el.get_text(" ", strip=True)

        # Parse key:value metadata from <dl><dt><dd> blocks
        fields: dict[str, str] = {}
        for dl in li.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                key = dt.get_text(strip=True).lower().rstrip(":").strip()
                val = dd.get_text(" ", strip=True)
                fields[key] = val

        # Also handle table-based layouts (some Idox versions)
        for row in li.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower().rstrip(":")
                val = cells[1].get_text(" ", strip=True)
                if key:
                    fields[key] = val

        # Fallback: address from fields if not found above
        if not address:
            address = (
                fields.get("address")
                or fields.get("site address")
                or fields.get("location")
                or ""
            )

        description = (
            fields.get("proposal")
            or fields.get("description")
            or fields.get("development description")
            or ""
        )
        app_type = (
            fields.get("application type")
            or fields.get("type")
            or fields.get("app type")
            or ""
        )
        status_raw = (
            fields.get("status")
            or fields.get("decision")
            or fields.get("current status")
            or ""
        )
        date_raw = (
            fields.get("date received")
            or fields.get("received")
            or fields.get("date validated")
            or fields.get("date registered")
            or fields.get("valid from date")
            or ""
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
            "council_name":     self.council_name,
            "council_url":      portal_url,
            "source":           "idox_scraper",
        }

    async def scrape(self, days_back: int = 7) -> list[dict]:
        """
        Main entry point: scrapes recent applications and returns them.
        Handles session, CSRF, search, and pagination.
        """
        d_to   = date.today()
        d_from = d_to - timedelta(days=days_back)
        str_from = d_from.strftime("%d/%m/%Y")
        str_to   = d_to.strftime("%d/%m/%Y")

        all_apps: list[dict] = []

        async with httpx.AsyncClient(
            headers={
                "User-Agent":      USER_AGENT,
                "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            },
            follow_redirects=True,
            timeout=20,
        ) as client:

            # Step 1 — establish session and get CSRF (empty string = no token needed)
            csrf = await self._init_session(client)
            await asyncio.sleep(REQ_DELAY)

            if csrf is None:
                # Hard failure — couldn't reach the portal
                return []

            # Step 2 — submit date-range search
            html = await self._post_search(client, csrf, str_from, str_to)
            await asyncio.sleep(REQ_DELAY)

            if not html:
                return []

            # Step 3 — parse pages
            page = 1
            while True:
                apps, has_next = self._parse_page(html)
                all_apps.extend(apps)

                if not has_next or not apps:
                    break

                page += 1
                if page > 50:  # safety cap
                    print(f"    ⚠ Hit 50-page cap")
                    break

                html = await self._get_page(client, page)
                await asyncio.sleep(REQ_DELAY)
                if not html:
                    break

            print(f"    {len(all_apps)} applications across {page} page(s)")

        return all_apps


# ---------------------------------------------------------------------------
# Per-council orchestration
# ---------------------------------------------------------------------------
async def process_council(
    portal: IdoxPortal,
    council_id: int,
    sem: asyncio.Semaphore,
    days_back: int,
) -> int:
    """Scrape, geocode, and upsert one council. Returns count saved."""
    async with sem:
        print(f"\n[{portal.council_name}]")
        try:
            apps = await portal.scrape(days_back)
        except Exception as e:
            print(f"    ✗ Scrape error: {e}")
            return 0

        if not apps:
            await _supa_patch_council(council_id, {
                "last_scraped_at": datetime.now(timezone.utc).isoformat()
            })
            return 0

        # Geocode postcodes that have no coordinates
        need = [a["postcode"] for a in apps if not a.get("lat") and a.get("postcode")]
        if need:
            print(f"    Geocoding {len(set(need))} postcodes…")
            coords = await geocode(need)
            for app in apps:
                if not app.get("lat") and app.get("postcode"):
                    pc = app["postcode"].strip().upper().replace(" ", "")
                    if pc in coords:
                        app["lat"], app["lng"] = coords[pc]

        # Build upsert records
        records = [
            {
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
            }
            for a in apps
        ]

        # Upsert in batches of 100
        ok = True
        for i in range(0, len(records), 100):
            if not await _supa_upsert(records[i:i + 100]):
                ok = False
                print(f"    ⚠ Batch upsert issue")

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
    # Import council list
    try:
        from idox_councils import IDOX_COUNCILS
    except ImportError:
        print("ERROR: idox_councils.py not found")
        sys.exit(1)

    bulk = "--bulk" in sys.argv
    days = int(os.environ.get("DAYS_BACK", "365" if bulk else "7"))

    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Idox scraper")
    print(f"Mode:        {'BULK' if bulk else 'FAST'} ({days} days back)")
    print(f"Councils:    {len(IDOX_COUNCILS)}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL else 'NOT SET'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    # Fetch DB councils ordered by oldest last_scraped_at first
    # so we always prioritise stale councils when time is short
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

    # Match council list to DB IDs
    to_scrape: list[tuple[IdoxPortal, int]] = []
    missing: list[str] = []

    for name, url in IDOX_COUNCILS:
        council_id = db_by_name.get(name.lower())
        if not council_id:
            # Try partial match
            for db_name, db_id in db_by_name.items():
                if name.lower() in db_name or db_name in name.lower():
                    council_id = db_id
                    break
        if council_id:
            to_scrape.append((IdoxPortal(name, url), council_id))
        else:
            missing.append(name)

    if missing:
        print(f"Not in DB — will skip: {', '.join(missing)}")
        print(f"  → Run the SQL in idox_councils.py to insert them\n")

    print(f"Scraping {len(to_scrape)} councils…\n")

    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = []
    skipped = 0

    for portal, council_id in to_scrape:
        if should_stop():
            skipped += 1
            continue
        tasks.append(process_council(portal, council_id, sem, days))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    total  = sum(r for r in results if isinstance(r, int))
    errors = sum(1 for r in results if isinstance(r, Exception))

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Applications saved: {total}")
    if errors:  print(f"Errors:             {errors}")
    if skipped: print(f"Skipped (time):     {skipped} councils")


if __name__ == "__main__":
    asyncio.run(main())
