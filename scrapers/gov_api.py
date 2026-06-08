"""
planning.data.gov.uk API poller.
Polls for new planning applications from the free government API.
No API key required. Open Government Licence.
"""
import httpx
from datetime import date, timedelta
from typing import Optional


API_BASE = "https://www.planning.data.gov.uk"


async def poll_gov_api(days_back: int = 2) -> list[dict]:
    """
    Fetch planning applications submitted in the last N days
    from the government planning data API.
    """
    since = date.today() - timedelta(days=days_back)
    applications = []
    offset = 0
    limit = 100

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                r = await client.get(
                    f"{API_BASE}/entity.json",
                    params={
                        "dataset": "planning-application",
                        "start_date_year": since.year,
                        "start_date_month": since.month,
                        "start_date_day": since.day,
                        "start_date_match": "since",
                        "limit": limit,
                        "offset": offset,
                    }
                )
                if r.status_code != 200:
                    print(f"  [Gov API] Returned {r.status_code}")
                    break

                data = r.json()
                entities = data.get("entities", [])
                if not entities:
                    break

                for entity in entities:
                    app = _parse_gov_entity(entity)
                    if app:
                        applications.append(app)

                if len(entities) < limit:
                    break
                offset += limit

            except Exception as e:
                print(f"  [Gov API] Error: {e}")
                break

    print(f"  [Gov API] Found {len(applications)} applications")
    return applications


def _parse_gov_entity(entity: dict) -> Optional[dict]:
    """Parse a planning application entity from the gov API."""
    props = entity.get("properties", entity)  # flat or nested

    reference = (
        props.get("reference")
        or props.get("application-reference")
        or str(entity.get("entity", ""))
    )
    if not reference:
        return None

    address = props.get("address") or props.get("site-address", "")
    description = props.get("description") or props.get("development-description", "")
    app_type = props.get("application-type") or props.get("type", "")
    status = props.get("status") or "pending"

    # Dates
    submitted_date = _parse_date_str(
        props.get("date-received") or props.get("start-date") or ""
    )
    decision_date = _parse_date_str(props.get("decision-date") or "")

    # Location
    point = props.get("point") or ""
    lat = lng = None
    if point:
        import re
        m = re.search(r"POINT\(([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\)", point)
        if m:
            lng = float(m.group(1))
            lat = float(m.group(2))

    # Council reference
    organisation = props.get("organisation") or ""

    return {
        "reference": str(reference),
        "address": address,
        "postcode": _extract_postcode(address),
        "lat": lat,
        "lng": lng,
        "description": description,
        "application_type": app_type,
        "status": status.lower() if status else "pending",
        "submitted_date": submitted_date,
        "decision_date": decision_date,
        "council_name": organisation,
        "source": "gov_api",
        "raw": entity,
    }


def _extract_postcode(text: str) -> Optional[str]:
    import re
    if not text:
        return None
    pattern = r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b"
    match = re.search(pattern, text.upper())
    return match.group(1).upper() if match else None


def _parse_date_str(s: str) -> Optional[date]:
    from datetime import datetime
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None
