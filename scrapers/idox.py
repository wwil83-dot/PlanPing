"""
Idox planning portal scraper.

Covers ~75 UK councils that use Idox Public Access.
URL pattern: https://[council]/online-applications/search.do

Uses the weekly list endpoint which returns all applications
submitted in the past week — perfect for nightly polling.
"""
import asyncio
import re
from datetime import date, datetime, timedelta
from typing import Optional
import httpx
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def idox_base_url(portal_url: str) -> str:
    """Extract base URL from a council's Idox portal URL."""
    # e.g. https://planning.bristol.gov.uk/online-applications/search.do?action=advanced
    # → https://planning.bristol.gov.uk/online-applications
    match = re.match(r"(https?://[^/]+/[^/]+)", portal_url)
    return match.group(1) if match else portal_url.rstrip("/")


async def scrape_idox_weekly(
    council_name: str,
    portal_url: str,
    days_back: int = 7,
) -> list[dict]:
    """
    Scrape recent planning applications from an Idox council portal.
    Returns list of application dicts.
    """
    base = idox_base_url(portal_url)
    weekly_url = f"{base}/search.do?action=weeklyList"
    applications = []

    async with httpx.AsyncClient(
        headers=HEADERS,
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        try:
            # Step 1 — hit the weekly list page to get session cookie
            r = await client.get(weekly_url)
            if r.status_code != 200:
                print(f"  [{council_name}] Weekly list returned {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")

            # Step 2 — submit the search form with date range
            form = soup.find("form", id="weeklyListForm") or soup.find("form")
            if not form:
                print(f"  [{council_name}] No search form found")
                return []

            # Build form data — date from last N days
            date_from = (date.today() - timedelta(days=days_back)).strftime("%d/%m/%Y")
            date_to = date.today().strftime("%d/%m/%Y")

            form_data = {
                "action": "weeklyList",
                "dateType": "DC_Validated",
                "dateFrom": date_from,
                "dateTo": date_to,
                "searchType": "Application",
            }

            # Add any hidden fields
            for hidden in form.find_all("input", type="hidden"):
                if hidden.get("name") and hidden.get("name") not in form_data:
                    form_data[hidden["name"]] = hidden.get("value", "")

            action = form.get("action", weekly_url)
            if not action.startswith("http"):
                # Relative URL
                action = f"{base}/{action.lstrip('/')}"

            r2 = await client.post(action, data=form_data)
            if r2.status_code != 200:
                print(f"  [{council_name}] Search POST returned {r2.status_code}")
                return []

            # Step 3 — parse results
            applications = _parse_idox_results(r2.text, base, portal_url)

            # Step 4 — paginate if needed
            page = 2
            while True:
                next_url = f"{base}/search.do?action=page&searchType=Application&resultsPerPage=100&page={page}"
                r3 = await client.get(next_url)
                if r3.status_code != 200:
                    break
                new_apps = _parse_idox_results(r3.text, base, portal_url)
                if not new_apps:
                    break
                applications.extend(new_apps)
                if len(new_apps) < 100:
                    break
                page += 1

        except Exception as e:
            print(f"  [{council_name}] Error: {e}")

    print(f"  [{council_name}] Found {len(applications)} applications")
    return applications


def _parse_idox_results(html: str, base_url: str, portal_url: str) -> list[dict]:
    """Parse application rows from an Idox results page."""
    soup = BeautifulSoup(html, "html.parser")
    applications = []

    # Idox results are in a table with class 'searchresults' or rows with id starting 'app'
    table = soup.find("table", class_="searchresults") or soup.find("ul", id="searchResults")

    if table is None:
        return []

    # Try table rows
    rows = table.find_all("tr") if table.name == "table" else table.find_all("li")

    for row in rows:
        try:
            app = _parse_idox_row(row, base_url)
            if app:
                applications.append(app)
        except Exception:
            continue

    return applications


def _parse_idox_row(row, base_url: str) -> Optional[dict]:
    """Parse a single result row from Idox."""
    # Reference number — usually in a link
    ref_link = row.find("a", href=lambda h: h and "applicationDetails" in str(h))
    if not ref_link:
        return None

    reference = ref_link.get_text(strip=True)
    if not reference or len(reference) < 5:
        return None

    detail_url = ref_link.get("href", "")
    if detail_url and not detail_url.startswith("http"):
        detail_url = f"{base_url}/{detail_url.lstrip('/')}"

    # Extract cells
    cells = row.find_all("td")
    if len(cells) < 4:
        return None

    texts = [c.get_text(strip=True) for c in cells]

    # Typical Idox column order: ref | address | description | type | date | status
    address = texts[1] if len(texts) > 1 else ""
    description = texts[2] if len(texts) > 2 else ""
    app_type = texts[3] if len(texts) > 3 else ""

    # Extract postcode from address
    postcode = _extract_postcode(address)

    # Parse submitted date
    submitted_date = None
    for text in texts:
        d = _parse_date(text)
        if d:
            submitted_date = d
            break

    return {
        "reference": reference,
        "address": address,
        "postcode": postcode,
        "description": description,
        "application_type": app_type,
        "status": "pending",
        "submitted_date": submitted_date,
        "council_url": detail_url,
        "source": "idox_scraper",
    }


def _extract_postcode(text: str) -> Optional[str]:
    """Extract a UK postcode from an address string."""
    pattern = r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b"
    match = re.search(pattern, text.upper())
    return match.group(1).upper() if match else None


def _parse_date(text: str) -> Optional[date]:
    """Try to parse a date from text."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None
