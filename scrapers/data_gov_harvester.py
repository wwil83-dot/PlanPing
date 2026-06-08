#!/usr/bin/env python3
"""
PlanPing — data.gov.uk harvester.
Downloads planning application open data feeds from councils via data.gov.uk CKAN API.
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
CKAN_API = "https://data.gov.uk/api/3/action"
HEADERS = {
    "User-Agent": "PlanPing/1.0 (+https://planping.onrender.com)",
    "Accept": "application/json, text/csv, */*",
}

# All the field name variants councils use
FIELD_MAPS = {
    "reference": [
        "application_reference","reference","app_ref","case_reference",
        "planning_reference","ref","application_number","appref",
        "applicationreference","case_ref","planning_ref","application_no",
        "app_no","appl_ref","casereference","app reference","reference number",
    ],
    "address": [
        "development_address","address","site_address","location",
        "site_location","property_address","address_of_proposal",
        "siteaddress","development_location","site address","premise",
        "full_address","location_text","development address",
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
        "decision_issued_date","decisiondate","date decided",
        "date_decided","decision date",
    ],
    "lat": ["latitude","lat","y_coord","northing","grid_ref_northing"],
    "lng": ["longitude","lng","lon","x_coord","easting","grid_ref_easting"],
}


def find_field(row: dict, field_key: str) -> Optional[str]:
    candidates = FIELD_MAPS.get(field_key, [field_key])
    row_lower = {k.lower().strip().replace(" ","_"): v for k, v in row.items()}
    # Also keep original lowercase
    row_lower_orig = {k.lower().strip(): v for k, v in row.items()}
    for candidate in candidates:
        val = row_lower.get(candidate.lower().replace(" ","_"))
        if val is None:
            val = row_lower_orig.get(candidate.lower())
        if val is not None and str(val).strip() not in ("", "None", "null", "NULL"):
            return str(val).strip()
    return None


async def search_ckan(client: httpx.AsyncClient, max_results: int = 500) -> list[dict]:
    """Search data.gov.uk for planning application datasets."""
    datasets = []
    start = 0

    while len(datasets) < max_results:
        try:
            r = await client.get(
                f"{CKAN_API}/package_search",
                params={"q": "planning applications", "rows": 100, "start": start},
                headers=HEADERS, timeout=30, follow_redirects=True,
            )
            if r.status_code != 200:
                print(f"  [CKAN] HTTP {r.status_code}")
                break

            data = r.json()
            results = data.get("result", {}).get("results", [])
            total = data.get("result", {}).get("count", 0)

            if not results:
                break

            datasets.extend(results)
            print(f"  [CKAN] {len(datasets)}/{total}")

            if len(datasets) >= total:
                break
            start += 100
            await asyncio.sleep(0.3)

        except Exception as e:
            print(f"  [CKAN] error: {e}")
            break

    return datasets


def is_planning_dataset(d: dict) -> bool:
    title = d.get("title", "").lower()
    return "planning application" in title and "boundary" not in title and "boundaries" not in title


def best_csv_resource(dataset: dict) -> Optional[dict]:
    """Get the best CSV or GeoJSON resource, preferring most recent year."""
    resources = dataset.get("resources", [])
    scored = []
    for res in resources:
        fmt = res.get("format", "").lower()
        url = res.get("url", "")
        if not url.startswith("http"):
            continue
        if fmt not in ("csv", "geojson", "json"):
            # Check URL extension
            if url.lower().endswith(".csv"):
                fmt = "csv"
            elif url.lower().endswith(".geojson"):
                fmt = "geojson"
            else:
                continue
        fmt_score = {"csv": 3, "geojson": 2, "json": 1}.get(fmt, 0)
        year_score = 0
        name = ((res.get("name") or "") + url).lower()
        for yr in range(2026, 2018, -1):
            if str(yr) in name:
                year_score = yr - 2018
                break
        scored.append((fmt_score + year_score, res, fmt))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0]
    best[1]["_detected_format"] = best[2]
    return best[1]


def dedup_by_council(datasets: list[dict]) -> list[dict]:
    """Keep only the best dataset per council organisation."""
    by_org = {}
    for d in datasets:
        org_id = d.get("organization", {}).get("id", "") or d.get("title", "")
        if org_id not in by_org:
            by_org[org_id] = d
        else:
            # Keep the one with the most recent resource
            existing = by_org[org_id]
            res_existing = best_csv_resource(existing)
            res_new = best_csv_resource(d)
            if res_existing and res_new:
                # Compare year scores
                def year_score(res):
                    name = ((res.get("name") or "") + (res.get("url") or "")).lower()
                    for yr in range(2026, 2018, -1):
                        if str(yr) in name:
                            return yr
                    return 0
                if year_score(res_new) > year_score(res_existing):
                    by_org[org_id] = d
    return list(by_org.values())


def parse_csv(content: str, council_name: str, source_url: str) -> list[dict]:
    apps = []
    cutoff = date.today() - timedelta(days=90)  # wider window — 90 days

    try:
        # Try to detect encoding issues
        try:
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
        except Exception:
            content = content.encode("latin-1").decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)

        if not rows:
            return []

        # Debug: show column names for first council
        cols = list(rows[0].keys()) if rows else []
        print(f"    Columns ({len(cols)}): {', '.join(cols[:8])}{'...' if len(cols)>8 else ''}")

        for row in rows:
            ref = find_field(row, "reference")
            if not ref:
                continue

            date_str = find_field(row, "submitted_date") or ""
            submitted = _parse_date(date_str)

            # Don't filter out rows with no date — keep them
            # Only exclude if date is present and old
            if submitted and submitted < cutoff:
                continue

            address = find_field(row, "address") or ""
            postcode = find_field(row, "postcode") or _extract_postcode(address)
            lat = _parse_coord(find_field(row, "lat"), "lat")
            lng = _parse_coord(find_field(row, "lng"), "lng")

            apps.append({
                "reference": ref.strip(),
                "address": address,
                "postcode": postcode,
                "lat": lat, "lng": lng,
                "description": find_field(row, "description") or "",
                "application_type": find_field(row, "application_type") or "",
                "status": _normalise_status(find_field(row, "status") or ""),
                "submitted_date": submitted,
                "decision_date": _parse_date(find_field(row, "decision_date") or ""),
                "council_name": council_name,
                "council_url": source_url,
                "source": "data_gov_uk",
            })

    except Exception as e:
        print(f"    CSV parse error: {e}")

    return apps


def parse_geojson(content: str, council_name: str, source_url: str) -> list[dict]:
    apps = []
    cutoff = date.today() - timedelta(days=90)

    try:
        data = json.loads(content)
        # Handle both FeatureCollection and array of features
        if isinstance(data, dict):
            features = data.get("features", [])
        elif isinstance(data, list):
            features = data
        else:
            return []

        if features:
            # Debug: show property keys
            sample_props = features[0].get("properties", {}) if isinstance(features[0], dict) else {}
            cols = list(sample_props.keys())
            print(f"    GeoJSON props ({len(cols)}): {', '.join(cols[:8])}{'...' if len(cols)>8 else ''}")

        for feature in features:
            if isinstance(feature, dict):
                props = feature.get("properties", {}) or {}
                geom = feature.get("geometry", {}) or {}
            else:
                continue

            ref = find_field(props, "reference")
            if not ref:
                continue

            date_str = find_field(props, "submitted_date") or ""
            submitted = _parse_date(date_str)
            if submitted and submitted < cutoff:
                continue

            lat = lng = None
            if geom.get("type") == "Point":
                coords = geom.get("coordinates", [])
                if len(coords) >= 2:
                    lng_raw, lat_raw = coords[0], coords[1]
                    # UK WGS84 coords
                    if 49 < lat_raw < 62 and -9 < lng_raw < 3:
                        lat, lng = lat_raw, lng_raw

            address = find_field(props, "address") or ""
            postcode = find_field(props, "postcode") or _extract_postcode(address)

            apps.append({
                "reference": ref.strip(),
                "address": address,
                "postcode": postcode,
                "lat": lat, "lng": lng,
                "description": find_field(props, "description") or "",
                "application_type": find_field(props, "application_type") or "",
                "status": _normalise_status(find_field(props, "status") or ""),
                "submitted_date": submitted,
                "decision_date": _parse_date(find_field(props, "decision_date") or ""),
                "council_name": council_name,
                "council_url": source_url,
                "source": "data_gov_uk",
            })

    except Exception as e:
        print(f"    GeoJSON parse error: {e}")

    return apps


async def download_and_parse(client, resource: dict, council_name: str) -> list[dict]:
    url = resource.get("url", "")
    fmt = resource.get("_detected_format", resource.get("format","").lower())

    try:
        r = await client.get(url, timeout=60, follow_redirects=True)
        if r.status_code != 200:
            print(f"    Download {r.status_code} — {url[:60]}")
            return []

        content = r.text
        print(f"    Downloaded {len(content):,} chars as {fmt}")

        if fmt == "csv" or (not fmt and not content.strip().startswith("{")):
            return parse_csv(content, council_name, url)
        else:
            return parse_geojson(content, council_name, url)

    except Exception as e:
        print(f"    Download error: {e}")
        return []


async def geocode_missing(apps: list[dict]) -> list[dict]:
    need = list({a["postcode"].strip().upper().replace(" ","")
                 for a in apps if not a.get("lat") and a.get("postcode")})
    if not need:
        return apps

    coords = {}
    print(f"    Geocoding {len(need)} postcodes...")
    for i in range(0, len(need), 100):
        chunk = need[i:i+100]
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.postcodes.io/postcodes",
                                 json={"postcodes": chunk})
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        coords[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"],
                        )
        except Exception as e:
            print(f"    Geocode error: {e}")
        await asyncio.sleep(0.3)

    for app in apps:
        if not app.get("lat") and app.get("postcode"):
            pc = app["postcode"].strip().upper().replace(" ", "")
            coord = coords.get(pc)
            if coord:
                app["lat"], app["lng"] = coord

    return apps


async def upsert(db, apps: list[dict]) -> tuple[int,int]:
    new = 0
    for app in apps:
        ref = (app.get("reference") or "").strip()
        council_name = app.get("council_name", "")
        if not ref or not council_name:
            continue

        council_id = await db.fetchval(
            "SELECT id FROM councils WHERE name ILIKE $1", f"%{council_name}%"
        )
        if not council_id:
            slug = re.sub(r"[^a-z0-9]+", "-", council_name.lower()).strip("-")
            try:
                council_id = await db.fetchval("""
                    INSERT INTO councils (name, slug, system, coverage_source)
                    VALUES ($1, $2, 'open_data', 'data_gov_uk')
                    ON CONFLICT (slug) DO UPDATE SET
                        coverage_source='data_gov_uk', updated_at=NOW()
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
                last_scraped_at=NOW(), updated_at=NOW() WHERE id=$1
            """, council_id)
        except Exception as e:
            print(f"    DB error {ref}: {e}")

    return len(apps), new


async def run_gov_api(db) -> int:
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
                        "limit": 100, "offset": offset,
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
                await asyncio.sleep(0.3)
            except Exception as ex:
                print(f"  Error: {ex}")
                break

    print(f"  Found {len(apps)} applications")
    if apps:
        apps = await geocode_missing(apps)
        _, new = await upsert(db, apps)
        print(f"  Saved {new} new")
    return len(apps)


async def main():
    pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=1, max_size=3,
        statement_cache_size=0, ssl="require"
    )
    start_time = datetime.utcnow()
    print(f"[{start_time.isoformat()}] PlanPing harvester starting...")

    total_apps = total_new = 0

    async with pool.acquire() as db:
        await run_gov_api(db)

        print("\n[data.gov.uk] Searching CKAN...")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            all_datasets = await search_ckan(client, max_results=500)

        print(f"  {len(all_datasets)} total datasets found")

        planning = [d for d in all_datasets if is_planning_dataset(d)]
        print(f"  {len(planning)} are planning application datasets")

        # Deduplicate — one dataset per council
        planning = dedup_by_council(planning)
        print(f"  {len(planning)} unique councils after dedup")

        async with httpx.AsyncClient(
            timeout=60, follow_redirects=True, headers=HEADERS
        ) as client:
            for dataset in planning:
                org = dataset.get("organization", {}).get("title", dataset.get("title",""))
                resource = best_csv_resource(dataset)

                if not resource:
                    print(f"  [{org}] No CSV/GeoJSON resource — skipping")
                    continue

                print(f"\n  [{org}] Downloading...")
                apps = await download_and_parse(client, resource, org)

                if not apps:
                    print(f"  [{org}] 0 applications parsed")
                    continue

                print(f"  [{org}] Parsed {len(apps)}. Geocoding...")
                apps = await geocode_missing(apps)

                async with pool.acquire() as db2:
                    found, new = await upsert(db2, apps)
                    total_apps += found
                    total_new += new
                    print(f"  [{org}] ✓ {new} new of {found}")

                await asyncio.sleep(0.5)

    elapsed = (datetime.utcnow() - start_time).seconds
    print(f"\nDone in {elapsed}s. Total={total_apps}, New={total_new}")
    await pool.close()


# ── helpers ──

def _parse_gov_entity(e: dict) -> Optional[dict]:
    ref = e.get("reference") or str(e.get("entity",""))
    if not ref:
        return None
    address = e.get("address","")
    lat = lng = None
    point = e.get("point","")
    if point:
        m = re.search(r"POINT\(([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\)", str(point))
        if m:
            lng, lat = float(m.group(1)), float(m.group(2))
    return {
        "reference": str(ref),
        "address": address,
        "postcode": _extract_postcode(address),
        "lat": lat, "lng": lng,
        "description": e.get("description",""),
        "application_type": e.get("application-type",""),
        "status": _normalise_status(e.get("status","")),
        "submitted_date": _parse_date(e.get("start-date","") or e.get("date-received","")),
        "council_name": e.get("organisation",""),
        "source": "planning_data_gov_uk",
    }


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _normalise_status(s: str) -> str:
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
    for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%d/%m/%y","%Y/%m/%d","%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_coord(s: Optional[str], kind: str) -> Optional[float]:
    if not s:
        return None
    try:
        val = float(s)
        if kind == "lat" and 49 < val < 62:
            return val
        if kind == "lng" and -9 < val < 3:
            return val
        return None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    asyncio.run(main())
