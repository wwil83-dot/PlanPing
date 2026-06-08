#!/usr/bin/env python3
"""
PlanPing — data.gov.uk harvester.

Uses the data.gov.uk CKAN API to:
1. Find all councils publishing planning application datasets
2. Download their CSV/GeoJSON feeds
3. Parse and upsert to the database

This is free, no API key needed, Open Government Licence.
Covers councils that publish open data — currently around 50-80 councils.

Run via GitHub Actions nightly alongside the gov API poller.
"""
import asyncio
import csv
import io
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

import asyncpg
import httpx

DATABASE_URL = os.environ["DATABASE_URL"]

# data.gov.uk CKAN API
CKAN_API = "https://data.gov.uk/api/3/action"

HEADERS = {
    "User-Agent": "PlanPing/1.0 (planning alerts service; +https://planping.onrender.com)",
    "Accept": "application/json",
}

# Known column name mappings across different council CSV formats
# Many councils use slightly different field names
FIELD_MAPS = {
    "reference": [
        "application_reference", "reference", "app_ref", "case_reference",
        "planning_reference", "ref", "application_number", "appref",
        "applicationreference", "case_ref", "planning_ref",
    ],
    "address": [
        "development_address", "address", "site_address", "location",
        "site_location", "property_address", "address_of_proposal",
        "siteaddress", "development_location",
    ],
    "postcode": [
        "postcode", "post_code", "site_postcode", "development_postcode",
    ],
    "description": [
        "development_description", "description", "proposal",
        "development_proposal", "application_description",
        "developmentdescription", "proposed_development",
    ],
    "application_type": [
        "application_type", "app_type", "type", "application_category",
        "type_of_application", "applicationtype",
    ],
    "status": [
        "decision", "status", "application_status", "outcome",
        "current_status", "decision_type", "determination",
    ],
    "submitted_date": [
        "date_received", "received_date", "date_valid", "valid_date",
        "submission_date", "date_submitted", "received", "datereceived",
        "date_of_application", "application_date",
    ],
    "decision_date": [
        "decision_date", "date_of_decision", "determination_date",
        "decision_issued_date", "decisiondate",
    ],
    "lat": ["latitude", "lat", "y", "northing"],
    "lng": ["longitude", "lng", "lon", "x", "easting"],
}


def find_field(row: dict, field_key: str) -> Optional[str]:
    """Find a value from a CSV row using known field name variants."""
    candidates = FIELD_MAPS.get(field_key, [field_key])
    row_lower = {k.lower().strip(): v for k, v in row.items()}
    for candidate in candidates:
        val = row_lower.get(candidate.lower())
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


async def search_ckan_datasets(client: httpx.AsyncClient, rows: int = 500) -> list[dict]:
    """
    Search data.gov.uk for planning application datasets.
    Returns list of dataset metadata dicts.
    """
    datasets = []
    start = 0

    while True:
        try:
            r = await client.get(
                f"{CKAN_API}/package_search",
                params={
                    "q": "planning applications",
                    "rows": 100,
                    "start": start,
                    "sort": "metadata_modified desc",
                },
                headers=HEADERS,
                timeout=30,
                follow_redirects=True,
            )
            if r.status_code != 200:
                print(f"  [CKAN] Search returned {r.status_code} — trying alternate URL")
                # Try the alternate data.gov.uk CKAN endpoint
                r = await client.get(
                    "https://ckan.publishing.service.gov.uk/api/3/action/package_search",
                    params={
                        "q": "planning applications",
                        "rows": 100,
                        "start": start,
                    },
                    headers=HEADERS,
                    timeout=30,
                    follow_redirects=True,
                )
                if r.status_code != 200:
                    print(f"  [CKAN] Alternate also returned {r.status_code}")
                    break

            data = r.json()
            results = data.get("result", {}).get("results", [])
            total = data.get("result", {}).get("count", 0)

            if not results:
                break

            datasets.extend(results)
            print(f"  [CKAN] Found {len(datasets)}/{total} datasets so far...")

            if len(datasets) >= total or len(datasets) >= rows:
                break
            start += 100
            await asyncio.sleep(0.5)

        except Exception as e:
            print(f"  [CKAN] Search error: {e}")
            break

    return datasets


def is_planning_applications_dataset(dataset: dict) -> bool:
    """Filter to datasets that are actually planning applications (not other planning data)."""
    title = dataset.get("title", "").lower()
    notes = dataset.get("notes", "").lower()
    tags = [t.get("name", "").lower() for t in dataset.get("tags", [])]

    # Must mention planning applications specifically
    planning_terms = ["planning application", "planning apps", "planning decisions"]
    has_planning = any(term in title or term in notes for term in planning_terms)

    # Exclude statistical/boundary datasets
    exclude_terms = ["statistics", "boundary", "boundaries", "constraints",
                     "conservation", "listed building", "flood", "tree preservation",
                     "infrastructure", "policy", "appeals", "enforcement"]
    is_excluded = any(term in title for term in exclude_terms)

    return has_planning and not is_excluded


def get_best_resource(dataset: dict) -> Optional[dict]:
    """Get the best downloadable resource from a dataset (prefer CSV, then GeoJSON)."""
    resources = dataset.get("resources", [])

    # Score by format preference
    format_score = {"csv": 3, "geojson": 2, "json": 1}
    scored = []

    for res in resources:
        fmt = res.get("format", "").lower()
        url = res.get("url", "")
        if fmt in format_score and url.startswith("http"):
            # Prefer recent data (check URL for year hints)
            year_score = 0
            for year in range(2026, 2020, -1):
                if str(year) in url or str(year) in res.get("name", ""):
                    year_score = year - 2020
                    break
            scored.append((format_score[fmt] + year_score, res))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def extract_council_name(dataset: dict) -> str:
    """Extract council name from dataset organization."""
    org = dataset.get("organization", {})
    title = org.get("title", "") or org.get("name", "")
    # Clean up common suffixes
    for suffix in [" Council", " Metropolitan Borough", " Borough Council",
                   " District Council", " City Council", " County Council",
                   " MBC", " LBC", " MDC"]:
        if title.endswith(suffix):
            title = title[:-len(suffix)] + suffix  # keep it but normalized
            break
    return title


def parse_csv_resource(content: str, council_name: str,
                        dataset_url: str, days_back: int = 30) -> list[dict]:
    """Parse a council planning applications CSV into standard format."""
    apps = []
    cutoff = date.today() - timedelta(days=days_back)

    try:
        # Try different encodings
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            try:
                ref = find_field(row, "reference")
                if not ref:
                    continue

                # Parse submitted date for filtering
                date_str = find_field(row, "submitted_date") or ""
                submitted = _parse_date(date_str)

                # Only include recent applications
                if submitted and submitted < cutoff:
                    continue

                address = find_field(row, "address") or ""
                postcode = find_field(row, "postcode") or _extract_postcode(address)

                # Try to get coordinates directly
                lat = _parse_float(find_field(row, "lat"))
                lng = _parse_float(find_field(row, "lng"))

                # If easting/northing, convert (approximate)
                if not lat and find_field(row, "lat"):
                    # Some councils use BNG eastings — skip for now, geocode by postcode
                    pass

                apps.append({
                    "reference": ref.strip(),
                    "address": address,
                    "postcode": postcode,
                    "lat": lat,
                    "lng": lng,
                    "description": find_field(row, "description") or "",
                    "application_type": find_field(row, "application_type") or "",
                    "status": _normalise_status(find_field(row, "status") or ""),
                    "submitted_date": submitted,
                    "decision_date": _parse_date(find_field(row, "decision_date") or ""),
                    "council_name": council_name,
                    "council_url": dataset_url,
                    "source": "data_gov_uk",
                })

            except Exception:
                continue

    except Exception as e:
        print(f"  CSV parse error for {council_name}: {e}")

    return apps


def parse_geojson_resource(content: str, council_name: str,
                            dataset_url: str, days_back: int = 30) -> list[dict]:
    """Parse a council planning applications GeoJSON into standard format."""
    apps = []
    cutoff = date.today() - timedelta(days=days_back)

    try:
        data = json.loads(content)
        features = data.get("features", [])

        for feature in features:
            try:
                props = feature.get("properties", {})
                geom = feature.get("geometry", {})

                ref = find_field(props, "reference")
                if not ref:
                    continue

                date_str = find_field(props, "submitted_date") or ""
                submitted = _parse_date(date_str)
                if submitted and submitted < cutoff:
                    continue

                # Extract coordinates from geometry
                lat = lng = None
                if geom.get("type") == "Point":
                    coords = geom.get("coordinates", [])
                    if len(coords) >= 2:
                        lng, lat = coords[0], coords[1]
                        # Sanity check — UK coordinates
                        if not (49 < lat < 61 and -8 < lng < 2):
                            lat = lng = None

                address = find_field(props, "address") or ""
                postcode = find_field(props, "postcode") or _extract_postcode(address)

                apps.append({
                    "reference": ref.strip(),
                    "address": address,
                    "postcode": postcode,
                    "lat": lat,
                    "lng": lng,
                    "description": find_field(props, "description") or "",
                    "application_type": find_field(props, "application_type") or "",
                    "status": _normalise_status(find_field(props, "status") or ""),
                    "submitted_date": submitted,
                    "decision_date": _parse_date(find_field(props, "decision_date") or ""),
                    "council_name": council_name,
                    "council_url": dataset_url,
                    "source": "data_gov_uk",
                })

            except Exception:
                continue

    except Exception as e:
        print(f"  GeoJSON parse error for {council_name}: {e}")

    return apps


async def download_and_parse(
    client: httpx.AsyncClient,
    resource: dict,
    council_name: str,
    days_back: int = 30,
) -> list[dict]:
    """Download a resource and parse it."""
    url = resource.get("url", "")
    fmt = resource.get("format", "").lower()

    try:
        r = await client.get(url, timeout=60, follow_redirects=True)
        if r.status_code != 200:
            print(f"  [{council_name}] Download returned {r.status_code}")
            return []

        content = r.text

        if fmt == "csv" or url.endswith(".csv"):
            return parse_csv_resource(content, council_name, url, days_back)
        elif fmt in ("geojson", "json") or url.endswith(".geojson"):
            return parse_geojson_resource(content, council_name, url, days_back)
        else:
            # Try CSV first, then GeoJSON
            if content.strip().startswith("{") or content.strip().startswith("["):
                return parse_geojson_resource(content, council_name, url, days_back)
            else:
                return parse_csv_resource(content, council_name, url, days_back)

    except Exception as e:
        print(f"  [{council_name}] Download error: {e}")
        return []


async def geocode_missing(apps: list[dict]) -> list[dict]:
    """Geocode applications that don't have coordinates but have postcodes."""
    need_geocoding = [
        a["postcode"].strip().upper().replace(" ", "")
        for a in apps
        if not a.get("lat") and a.get("postcode")
    ]

    if not need_geocoding:
        return apps

    coords = {}
    unique = list(set(need_geocoding))
    print(f"  Geocoding {len(unique)} postcodes...")

    for i in range(0, len(unique), 100):
        chunk = unique[i:i+100]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": chunk}
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        coords[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"]
                        )
        except Exception as e:
            print(f"  Geocode error: {e}")
        await asyncio.sleep(0.3)

    # Apply coordinates
    for app in apps:
        if not app.get("lat") and app.get("postcode"):
            pc = app["postcode"].strip().upper().replace(" ", "")
            coord = coords.get(pc)
            if coord:
                app["lat"], app["lng"] = coord

    return apps


async def upsert_to_db(db, apps: list[dict]) -> tuple[int, int]:
    """Upsert applications. Returns (total, new)."""
    new_count = 0

    for app in apps:
        ref = app.get("reference", "").strip()
        council_name = app.get("council_name", "")

        if not ref or not council_name:
            continue

        # Find council in DB
        council_id = await db.fetchval(
            "SELECT id FROM councils WHERE name ILIKE $1 OR name ILIKE $2",
            f"%{council_name}%",
            f"{council_name.split(' Council')[0]}%",
        )

        if not council_id:
            # Try to insert the council if it doesn't exist
            try:
                council_id = await db.fetchval("""
                    INSERT INTO councils (name, slug, system, coverage_source)
                    VALUES ($1, $2, 'open_data', 'data_gov_uk')
                    ON CONFLICT (slug) DO UPDATE SET
                        coverage_source = 'data_gov_uk',
                        updated_at = NOW()
                    RETURNING id
                """,
                    council_name,
                    re.sub(r'[^a-z0-9]+', '-', council_name.lower()).strip('-'),
                )
            except Exception:
                continue

        try:
            result = await db.execute("""
                INSERT INTO planning_applications (
                    council_id, reference, address, postcode, lat, lng,
                    description, application_type, status,
                    submitted_date, decision_date, council_url, source
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT (council_id, reference) DO UPDATE SET
                    status       = EXCLUDED.status,
                    decision_date = COALESCE(EXCLUDED.decision_date, planning_applications.decision_date),
                    lat          = COALESCE(EXCLUDED.lat, planning_applications.lat),
                    lng          = COALESCE(EXCLUDED.lng, planning_applications.lng),
                    updated_at   = NOW()
            """,
                council_id,
                ref,
                app.get("address"),
                app.get("postcode"),
                app.get("lat"),
                app.get("lng"),
                app.get("description"),
                app.get("application_type"),
                app.get("status", "pending"),
                app.get("submitted_date"),
                app.get("decision_date"),
                app.get("council_url"),
                app.get("source", "data_gov_uk"),
            )
            if result and "INSERT" in result and not result.endswith(" 0"):
                new_count += 1

            # Update council coverage
            await db.execute("""
                UPDATE councils SET coverage_source = 'data_gov_uk',
                last_scraped_at = NOW(), updated_at = NOW()
                WHERE id = $1
            """, council_id)

        except Exception as e:
            print(f"  DB error for {ref}: {e}")

    return len(apps), new_count


async def run_gov_api(db) -> int:
    """Poll planning.data.gov.uk for recent applications."""
    print("\n[planning.data.gov.uk] Polling...")
    apps = []
    since = date.today() - timedelta(days=7)
    offset = 0

    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        while True:
            try:
                r = await client.get(
                    "https://www.planning.data.gov.uk/entity.json",
                    params={
                        "dataset": "planning-application",
                        "start_date_year": since.year,
                        "start_date_month": since.month,
                        "start_date_day": since.day,
                        "start_date_match": "since",
                        "limit": 100,
                        "offset": offset,
                    }
                )
                if r.status_code != 200:
                    break
                data = r.json()
                entities = data.get("entities", [])
                if not entities:
                    break

                for e in entities:
                    app = _parse_gov_entity(e)
                    if app:
                        apps.append(app)

                if len(entities) < 100:
                    break
                offset += 100
                await asyncio.sleep(0.5)
            except Exception as ex:
                print(f"  Error: {ex}")
                break

    print(f"  [planning.data.gov.uk] Found {len(apps)} applications")
    if apps:
        apps = await geocode_missing(apps)
        _, new = await upsert_to_db(db, apps)
        print(f"  [planning.data.gov.uk] Saved {new} new applications")
    return len(apps)


async def main():
    pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=1, max_size=3,
        statement_cache_size=0, ssl="require"
    )
    start_time = datetime.utcnow()
    print(f"[{start_time.isoformat()}] PlanPing data.gov.uk harvester starting...")

    total_apps = 0
    total_new = 0

    async with pool.acquire() as db:
        # 1. Poll planning.data.gov.uk API
        await run_gov_api(db)

        # 2. Harvest data.gov.uk council feeds
        print("\n[data.gov.uk] Searching for planning application datasets...")

        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            datasets = await search_ckan_datasets(client, rows=500)

        print(f"  Found {len(datasets)} total datasets")

        # Debug: show first few titles
        for d in datasets[:8]:
            print(f"    Sample: {d.get("title", "no title")}")

        # Filter to actual planning application datasets
        planning_datasets = [d for d in datasets if is_planning_applications_dataset(d)]
        print(f"  {len(planning_datasets)} are planning application datasets")

        # Process each dataset
        async with httpx.AsyncClient(
            timeout=60,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            for dataset in planning_datasets:
                council_name = extract_council_name(dataset)
                resource = get_best_resource(dataset)

                if not resource:
                    continue

                print(f"  Downloading {council_name}...")
                apps = await download_and_parse(client, resource, council_name, days_back=30)

                if not apps:
                    continue

                print(f"  [{council_name}] Parsed {len(apps)} applications. Geocoding...")
                apps = await geocode_missing(apps)

                async with pool.acquire() as db:
                    found, new = await upsert_to_db(db, apps)
                    total_apps += found
                    total_new += new
                    print(f"  [{council_name}] ✓ saved {new} new of {found}")

                await asyncio.sleep(1)  # polite delay between councils

    elapsed = (datetime.utcnow() - start_time).seconds
    print(f"\nDone in {elapsed}s. Total={total_apps}, New={total_new}")
    await pool.close()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _parse_gov_entity(entity: dict) -> Optional[dict]:
    ref = entity.get("reference") or str(entity.get("entity", ""))
    if not ref:
        return None
    address = entity.get("address") or ""
    lat = lng = None
    point = entity.get("point", "")
    if point:
        m = re.search(r"POINT\(([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\)", str(point))
        if m:
            lng, lat = float(m.group(1)), float(m.group(2))
    return {
        "reference": str(ref),
        "address": address,
        "postcode": _extract_postcode(address),
        "lat": lat, "lng": lng,
        "description": entity.get("description", ""),
        "application_type": entity.get("application-type", ""),
        "status": _normalise_status(entity.get("status", "")),
        "submitted_date": _parse_date(entity.get("start-date", "") or entity.get("date-received", "")),
        "council_name": entity.get("organisation", ""),
        "source": "planning_data_gov_uk",
    }


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _normalise_status(s: str) -> str:
    s = (s or "").lower()
    if any(x in s for x in ("approv", "grant", "permit", "allowed")):
        return "approved"
    if any(x in s for x in ("refus", "reject", "dismiss")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
    return "pending"


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y",
                "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        val = float(s)
        # UK lat range 49-61, lng -8 to 2
        if 49 < val < 61 or -8 < val < 2:
            return val
        return None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    asyncio.run(main())
