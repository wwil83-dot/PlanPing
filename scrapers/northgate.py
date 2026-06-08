"""
Northgate Planning Explorer scraper.

Covers ~8 UK councils including Birmingham and Camden.
URL pattern: https://[council]/Northgate/PlanningExplorer/GeneralSearch.aspx
"""
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
}


def northgate_base_url(portal_url: str) -> str:
    """Extract base URL from Northgate portal URL."""
    match = re.match(r"(https?://[^/]+/Northgate/PlanningExplorer)", portal_url)
    return match.group(1) if match else portal_url.split("GeneralSearch")[0]


async def scrape_northgate_weekly(
    council_name: str,
    portal_url: str,
    days_back: int = 7,
) -> list[dict]:
    """
    Scrape recent planning applications from a Northgate council portal.
    Returns list of application dicts.
    """
    base = northgate_base_url(portal_url)
    search_url = f"{base}/GeneralSearch.aspx"
    applications = []

    date_from = (date.today() - timedelta(days=days_back)).strftime("%d/%m/%Y")
    date_to = date.today().strftime("%d/%m/%Y")

    async with httpx.AsyncClient(
        headers=HEADERS,
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        try:
            # Step 1 — get the search page (need ASP.NET viewstate)
            r = await client.get(search_url)
            if r.status_code != 200:
                print(f"  [{council_name}] Search page returned {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")

            # Extract ASP.NET hidden fields
            form_data = {}
            for hidden in soup.find_all("input", type="hidden"):
                name = hidden.get("name", "")
                if name:
                    form_data[name] = hidden.get("value", "")

            # Add date range search fields
            form_data.update({
                "cboSelectDateRange": "DATE_RECEIVED",
                "txtDateReceivedStart": date_from,
                "txtDateReceivedEnd": date_to,
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "btnSearch": "Search",
            })

            # Step 2 — submit search
            r2 = await client.post(search_url, data=form_data)
            if r2.status_code != 200:
                print(f"  [{council_name}] Search POST returned {r2.status_code}")
                return []

            applications = _parse_northgate_results(r2.text, base, portal_url)

        except Exception as e:
            print(f"  [{council_name}] Error: {e}")

    print(f"  [{council_name}] Found {len(applications)} applications")
    return applications


def _parse_northgate_results(html: str, base_url: str, portal_url: str) -> list[dict]:
    """Parse application rows from a Northgate results page."""
    soup = BeautifulSoup(html, "html.parser")
    applications = []

    # Northgate results table
    table = soup.find("table", id=re.compile(r"gvResults|searchResults", re.I))
    if not table:
        table = soup.find("table", class_=re.compile(r"result", re.I))
    if not table:
        return []

    rows = table.find_all("tr")[1:]  # skip header

    for row in rows:
        try:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Find reference link
            ref_link = row.find("a", href=lambda h: h and "ApplicationDetails" in str(h))
            if not ref_link:
                ref_link = row.find("a")
            if not ref_link:
                continue

            reference = ref_link.get_text(strip=True)
            if not reference:
                continue

            detail_href = ref_link.get("href", "")
            detail_url = detail_href if detail_href.startswith("http") else f"{base_url}/{detail_href.lstrip('/')}"

            texts = [c.get_text(strip=True) for c in cells]
            address = texts[1] if len(texts) > 1 else ""
            description = texts[2] if len(texts) > 2 else ""
            app_type = texts[3] if len(texts) > 3 else ""
            postcode = _extract_postcode(address)

            submitted_date = None
            for text in texts:
                d = _parse_date(text)
                if d:
                    submitted_date = d
                    break

            applications.append({
                "reference": reference,
                "address": address,
                "postcode": postcode,
                "description": description,
                "application_type": app_type,
                "status": "pending",
                "submitted_date": submitted_date,
                "council_url": detail_url,
                "source": "northgate_scraper",
            })
        except Exception:
            continue

    return applications


def _extract_postcode(text: str) -> Optional[str]:
    pattern = r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b"
    match = re.search(pattern, text.upper())
    return match.group(1).upper() if match else None


def _parse_date(text: str) -> Optional[date]:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None
