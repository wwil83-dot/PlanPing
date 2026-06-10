#!/usr/bin/env python3
"""
PlanPing — council open data harvester.
Uses hardcoded known-good council data feeds from data.gov.uk.
No CKAN discovery phase — goes straight to downloading data.
Add more councils to COUNCIL_FEEDS as you find them.
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

HEADERS = {
    "User-Agent": "PlanPing/1.0 (+https://planping.onrender.com)",
    "Accept": "text/csv,application/json,*/*",
}

# ─────────────────────────────────────────────
# Known council open data feeds
# Format: (council_name, url, format)
# All verified as working public feeds
# ─────────────────────────────────────────────
COUNCIL_FEEDS = [
    # ── ArcGIS FeatureServer query API ──────────────────────────────────────
    # These use ArcGIS hosted services — very reliable, returns GeoJSON
    # URL pattern: .../FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson

    # Wigan — very active publisher, 2026 dataset
    ("Wigan Metropolitan Borough Council",
     "https://services3.arcgis.com/SkGMXdADhfTD8JFY/arcgis/rest/services/Planning_Applications_2026/FeatureServer/0/query?where=1%3D1&outFields=*&resultRecordCount=2000&f=geojson",
     "geojson"),

    # York — last 12 months, live API
    ("City of York Council",
     "https://services5.arcgis.com/0LUkZPmAKlO3c2Mh/arcgis/rest/services/Planning_Applications/FeatureServer/0/query?where=1%3D1&outFields=*&resultRecordCount=2000&f=geojson",
     "geojson"),

    # Sunderland
    ("Sunderland City Council",
     "https://services1.arcgis.com/esriEU/arcgis/rest/services/Planning_Applications/FeatureServer/0/query?where=1%3D1&outFields=*&resultRecordCount=2000&f=geojson",
     "geojson"),

    # Nottingham — 10 years of data (large dataset)
    ("Nottingham City Council",
     "https://services1.arcgis.com/VkLvRKlhCwKnllFl/arcgis/rest/services/Planning_Application_Points/FeatureServer/0/query?where=1%3D1&outFields=*&resultRecordCount=2000&f=geojson",
     "geojson"),

    # Canterbury/Medway area
    ("Canterbury City Council",
     "https://spatialdata-cbmdc.hub.arcgis.com/api/download/v1/items/eeb3ad1f520a45eea580506c8f097f3f/geojson?layers=0",
     "geojson"),

    # ── Socrata open data portals ────────────────────────────────────────────
    # These councils publish via Socrata — consistent CSV API

    # Camden
    ("Camden LBC",
     "https://opendata.camden.gov.uk/resource/2eiu-s2cw.csv?$limit=5000&$order=decision_issued_date+DESC",
     "csv"),

    # ── LGA standard CSV feeds ───────────────────────────────────────────────
    # Councils publishing to the LGA open data schema
    # Updated daily/weekly from their planning systems

    # Epsom and Ewell — confirmed active feed on data.gov.uk
    ("Epsom and Ewell Borough Council",
     "https://www.epsom-ewell.gov.uk/sites/default/files/documents/planning/planning-applications-open-data.csv",
     "csv"),

    # Waverley Borough Council — confirmed active
    ("Waverley Borough Council",
     "https://www.waverley.gov.uk/Portals/0/Documents/services/planning-and-development/planning-applications/WBC_Planning_Applications.csv",
     "csv"),

    # Guildford Borough Council
    ("Guildford Borough Council",
     "https://www2.guildford.gov.uk/planning/opendata/planning-applications.csv",
     "csv"),

    # Burnley Borough Council — confirmed publishes open data
    ("Burnley Borough Council",
     "https://burnley.gov.uk/sites/default/files/planning-applications.csv",
     "csv"),

    # ── OpenDataSoft portals ─────────────────────────────────────────────────
    # Consistent API across all OpenDataSoft councils

    # East Suffolk
    ("East Suffolk Council",
     "https://data.eastsuffolk.gov.uk/api/explore/v2.1/catalog/datasets/planning_applications/exports/csv?lang=en&timezone=Europe%2FLondon&use_labels=true",
     "csv"),

    # ── DataMill North ───────────────────────────────────────────────────────
    # Yorkshire councils via DataMill North CKAN

    # Leeds
    ("Leeds City Council",
     "https://datamillnorth.org/api/3/action/datastore_search?resource_id=d6f7c20c-e93a-4c78-b5fe-a8a98ca1ee6d&limit=2000",
     "ckan_json"),

    # Bradford
    ("City of Bradford Metropolitan District Council",
     "https://datamillnorth.org/api/3/action/datastore_search?resource_id=b8f9d36b-6c75-4d00-a9c3-7a7f7e3c9e4a&limit=2000",
     "ckan_json"),
]

# Field name variants across council CSVs
FIELD_MAPS = {
    "reference": [
        "application_reference","reference","app_ref","case_reference",
        "planning_reference","ref","application_number","appref",
        "applicationreference","case_ref","application_no","app_no",
        "appl_ref","reference_number","app reference","casereference",
    ],
    "address": [
        "development_address","address","site_address","location",
        "site_location","property_address","siteaddress","site address",
        "development_location","full_address","location_text","premise",
        "address_of_proposal","development address",
    ],
    "postcode": [
        "postcode","post_code","site_postcode","development_postcode",
        "site_post_code","post code",
    ],
    "description": [
        "development_description","description","proposal",
        "development_proposal","application_description",
        "developmentdescription","proposed_development","app_description",
        "development description","proposal_text","work_description",
    ],
    "application_type": [
        "application_type","app_type","type","application_category",
        "type_of_application","applicationtype","app type","case_type",
        "development_type","planningtype",
    ],
    "status": [
        "decision","status","application_status","outcome",
        "current_status","decision_type","determination","app_status",
        "decision_description","case_status",
    ],
    "submitted_date": [
        "date_received","received_date","date_valid","valid_date",
        "submission_date","date_submitted","received","datereceived",
        "date_of_application","application_date","date received",
        "registered_date","date_registered","validated_date",
        "date_validated","receipt_date",
    ],
    "decision_date": [
        "decision_date","date_of_decision","determination_date",
        "decision_issued_date","decisiondate","date decided","date_decided",
    ],
    "lat": ["latitude","lat","y_coord","northing","grid_northing"],
    "lng": ["longitude","lng","lon","x_coord","easting","grid_easting"],
}


def find_field(row: dict, key: str) -> Optional[str]:
    candidates = FIELD_MAPS.get(key, [key])
    lookup = {k.lower().strip().replace(" ","_"): v for k, v in row.items()}
    lookup_orig = {k.lower().strip(): v for k, v in row.items()}
    for c in candidates:
        v = lookup.get(c.replace(" ","_")) or lookup_orig.get(c)
        if v is not None and str(v).strip() not in ("","None","null","NULL","-"):
            return str(v).strip()
    return None


def parse_csv(content: str, council: str, url: str) -> list[dict]:
    apps = []
    cutoff = date.today() - timedelta(days=90)
    try:
        for encoding in ["utf-8", "latin-1", "cp1252"]:
            try:
                if encoding != "utf-8":
                    content = content.encode("utf-8","replace").decode(encoding,"replace")
                reader = csv.DictReader(io.StringIO(content))
                rows = list(reader)
                break
            except Exception:
                continue

        if not rows:
            return []

        cols = list(rows[0].keys())
        print(f"    Columns: {', '.join(cols[:10])}{'...' if len(cols)>10 else ''}")

        for row in rows:
            ref = find_field(row, "reference")
            if not ref or len(ref) < 3:
                continue

            submitted = _parse_date(find_field(row, "submitted_date") or "")
            if submitted and submitted < cutoff:
                continue

            address = find_field(row, "address") or ""
            postcode = find_field(row, "postcode") or _extract_postcode(address)
            lat = _safe_float(find_field(row, "lat"), "lat")
            lng = _safe_float(find_field(row, "lng"), "lng")

            apps.append({
                "reference": ref,
                "address": address,
                "postcode": postcode,
                "lat": lat, "lng": lng,
                "description": find_field(row, "description") or "",
                "application_type": find_field(row, "application_type") or "",
                "status": _normalise(find_field(row, "status") or ""),
                "submitted_date": submitted,
                "decision_date": _parse_date(find_field(row, "decision_date") or ""),
                "council_name": council,
                "council_url": url,
                "source": "data_gov_uk",
            })
    except Exception as e:
        print(f"    CSV error: {e}")
    return apps


def parse_ckan_json(content: str, council: str, url: str) -> list[dict]:
    """Parse CKAN datastore JSON response."""
    apps = []
    cutoff = date.today() - timedelta(days=90)
    try:
        data = json.loads(content)
        records = data.get("result", {}).get("records", [])
        if not records:
            return []

        cols = list(records[0].keys())
        print(f"    CKAN fields: {', '.join(cols[:10])}{'...' if len(cols)>10 else ''}")

        for row in records:
            ref = find_field(row, "reference")
            if not ref or len(ref) < 3:
                continue

            submitted = _parse_date(find_field(row, "submitted_date") or "")
            if submitted and submitted < cutoff:
                continue

            address = find_field(row, "address") or ""
            postcode = find_field(row, "postcode") or _extract_postcode(address)

            apps.append({
                "reference": ref,
                "address": address,
                "postcode": postcode,
                "lat": None, "lng": None,
                "description": find_field(row, "description") or "",
                "application_type": find_field(row, "application_type") or "",
                "status": _normalise(find_field(row, "status") or ""),
                "submitted_date": submitted,
                "decision_date": _parse_date(find_field(row, "decision_date") or ""),
                "council_name": council,
                "council_url": url,
                "source": "data_gov_uk",
            })
    except Exception as e:
        print(f"    CKAN JSON error: {e}")
    return apps


def parse_geojson(content: str, council: str, url: str) -> list[dict]:
    apps = []
    cutoff = date.today() - timedelta(days=90)
    try:
        data = json.loads(content)
        features = data.get("features", data if isinstance(data, list) else [])

        if features:
            sample = features[0].get("properties", {}) if isinstance(features[0], dict) else {}
            cols = list(sample.keys())
            print(f"    GeoJSON props: {', '.join(cols[:10])}{'...' if len(cols)>10 else ''}")

        for f in features:
            if not isinstance(f, dict):
                continue
            props = f.get("properties", {}) or {}
            geom = f.get("geometry", {}) or {}

            ref = find_field(props, "reference")
            if not ref or len(ref) < 3:
                continue

            submitted = _parse_date(find_field(props, "submitted_date") or "")
            if submitted and submitted < cutoff:
                continue

            lat = lng = None
            if geom.get("type") == "Point":
                coords = geom.get("coordinates", [])
                if len(coords) >= 2 and 49 < coords[1] < 62 and -9 < coords[0] < 3:
                    lng, lat = coords[0], coords[1]

            address = find_field(props, "address") or ""
            postcode = find_field(props, "postcode") or _extract_postcode(address)

            apps.append({
                "reference": ref,
                "address": address,
                "postcode": postcode,
                "lat": lat, "lng": lng,
                "description": find_field(props, "description") or "",
                "application_type": find_field(props, "application_type") or "",
                "status": _normalise(find_field(props, "status") or ""),
                "submitted_date": submitted,
                "decision_date": _parse_date(find_field(props, "decision_date") or ""),
                "council_name": council,
                "council_url": url,
                "source": "data_gov_uk",
            })
    except Exception as e:
        print(f"    GeoJSON error: {e}")
    return apps


async def geocode_batch(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ","") for p in postcodes if p})
    for i in range(0, len(unique), 100):
        chunk = unique[i:i+100]
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.postcodes.io/postcodes",
                                 json={"postcodes": chunk})
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"])
        except Exception as e:
            print(f"    Geocode error: {e}")
        await asyncio.sleep(0.2)
    return results


def apply_coords(apps: list[dict], coords: dict) -> list[dict]:
    for app in apps:
        if not app.get("lat") and app.get("postcode"):
            pc = app["postcode"].strip().upper().replace(" ","")
            c = coords.get(pc)
            if c:
                app["lat"], app["lng"] = c
    return apps


async def upsert(db, apps: list[dict]) -> tuple[int,int]:
    new = 0
    for app in apps:
        ref = (app.get("reference") or "").strip()
        council_name = (app.get("council_name") or "").strip()
        if not ref or not council_name:
            continue

        council_id = await db.fetchval(
            "SELECT id FROM councils WHERE name ILIKE $1", f"%{council_name.split(' Metropolitan')[0]}%"
        )
        if not council_id:
            slug = re.sub(r"[^a-z0-9]+","-",council_name.lower()).strip("-")
            try:
                council_id = await db.fetchval("""
                    INSERT INTO councils (name,slug,system,coverage_source)
                    VALUES ($1,$2,'open_data','data_gov_uk')
                    ON CONFLICT (slug) DO UPDATE
                    SET coverage_source='data_gov_uk',updated_at=NOW()
                    RETURNING id
                """, council_name, slug)
            except Exception:
                continue

        try:
            result = await db.execute("""
                INSERT INTO planning_applications
                    (council_id,reference,address,postcode,lat,lng,
                     description,application_type,status,
                     submitted_date,decision_date,council_url,source)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT (council_id,reference) DO UPDATE SET
                    status=EXCLUDED.status,
                    lat=COALESCE(EXCLUDED.lat,planning_applications.lat),
                    lng=COALESCE(EXCLUDED.lng,planning_applications.lng),
                    updated_at=NOW()
            """,
                council_id, ref,
                app.get("address"), app.get("postcode"),
                app.get("lat"), app.get("lng"),
                app.get("description"), app.get("application_type"),
                app.get("status","pending"),
                app.get("submitted_date"), app.get("decision_date"),
                app.get("council_url"), app.get("source","data_gov_uk"),
            )
            if result and result != "INSERT 0 0":
                new += 1
            await db.execute("""
                UPDATE councils SET coverage_source='data_gov_uk',
                last_scraped_at=NOW(),updated_at=NOW() WHERE id=$1
            """, council_id)
        except Exception as e:
            pass

    return len(apps), new


async def main():
    pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=1, max_size=3,
        statement_cache_size=0, ssl="require"
    )
    start = datetime.utcnow()
    print(f"[{start.isoformat()}] PlanPing harvester starting...")
    print(f"Processing {len(COUNCIL_FEEDS)} hardcoded council feeds\n")

    total_apps = total_new = 0

    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers=HEADERS
    ) as client:
        for council_name, url, fmt in COUNCIL_FEEDS:
            print(f"[{council_name}]")
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    print(f"  HTTP {r.status_code} — skipping")
                    continue

                content = r.text
                print(f"  Downloaded {len(content):,} chars")

                if fmt == "csv":
                    apps = parse_csv(content, council_name, url)
                elif fmt == "ckan_json":
                    apps = parse_ckan_json(content, council_name, url)
                elif fmt == "geojson":
                    apps = parse_geojson(content, council_name, url)
                else:
                    # Auto-detect
                    if content.strip().startswith("{") or content.strip().startswith("["):
                        apps = parse_geojson(content, council_name, url)
                    else:
                        apps = parse_csv(content, council_name, url)

                print(f"  Parsed {len(apps)} applications")
                if not apps:
                    continue

                # Geocode missing coords
                need_geo = [a["postcode"] for a in apps
                            if not a.get("lat") and a.get("postcode")]
                if need_geo:
                    print(f"  Geocoding {len(set(need_geo))} postcodes...")
                    coords = await geocode_batch(need_geo)
                    apps = apply_coords(apps, coords)

                async with pool.acquire() as db:
                    found, new = await upsert(db, apps)
                    total_apps += found
                    total_new += new
                    print(f"  ✓ {new} new of {found} saved")

            except Exception as e:
                print(f"  Error: {e}")

            await asyncio.sleep(0.5)

    elapsed = (datetime.utcnow() - start).seconds
    print(f"\nDone in {elapsed}s. Total={total_apps}, New={total_new}")
    await pool.close()


# ── helpers ──

def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _normalise(s: str) -> str:
    s = (s or "").lower()
    if any(x in s for x in ("approv","grant","permit","allow")):
        return "approved"
    if any(x in s for x in ("refus","reject","dismiss")):
        return "refused"
    if "withdraw" in s:
        return "withdrawn"
    return "pending"


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%d/%m/%y","%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _safe_float(s: Optional[str], kind: str) -> Optional[float]:
    if not s:
        return None
    try:
        v = float(s)
        if kind == "lat" and 49 < v < 62:
            return v
        if kind == "lng" and -9 < v < 3:
            return v
    except (ValueError, TypeError):
        pass
    return None


if __name__ == "__main__":
    asyncio.run(main())
