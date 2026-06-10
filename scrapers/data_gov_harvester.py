#!/usr/bin/env python3
"""
PlanPing — council open data harvester.
Uses psycopg2 (sync) for reliability with Supabase from GitHub Actions.
"""
import asyncio
import csv
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]
if "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

HEADERS = {
    "User-Agent": "PlanPing/1.0 (+https://planping.onrender.com)",
    "Accept": "text/csv,application/json,*/*",
}

COUNCIL_FEEDS = [
    # Camden — limited to 2000 most recent
    ("Camden LBC",
     "https://opendata.camden.gov.uk/resource/2eiu-s2cw.csv?$limit=2000&$order=registered_date+DESC",
     "csv"),
]

BULK_FEEDS = [
    ("Camden LBC",
     "https://opendata.camden.gov.uk/api/views/2eiu-s2cw/rows.csv?accessType=DOWNLOAD",
     "csv"),
    ("Canterbury City Council",
     "https://spatialdata-cbmdc.hub.arcgis.com/api/download/v1/items/eeb3ad1f520a45eea580506c8f097f3f/csv?layers=0",
     "csv"),
]

FIELD_MAPS = {
    "reference": [
        "application_reference","reference","app_ref","case_reference",
        "planning_reference","ref","application_number","appref",
        "applicationreference","case_ref","application_no","app_no",
        "appl_ref","reference_number","casereference",
        "REFVAL","KEYVAL","Application Number","pk",
    ],
    "address": [
        "development_address","address","site_address","location",
        "site_location","property_address","siteaddress",
        "development_location","full_address","premise",
        "address_of_proposal","ADDRESS","Development Address",
    ],
    "postcode": [
        "postcode","post_code","site_postcode","development_postcode",
    ],
    "description": [
        "development_description","description","proposal",
        "development_proposal","application_description",
        "proposed_development","Development Description",
    ],
    "application_type": [
        "application_type","app_type","type","application_category",
        "type_of_application","applicationtype","case_type",
    ],
    "status": [
        "decision","status","application_status","outcome",
        "current_status","decision_type","determination",
        "DCSTAT","DECSN","Decision Type",
    ],
    "submitted_date": [
        "date_received","received_date","date_valid","valid_date",
        "submission_date","date_submitted","received","datereceived",
        "date_of_application","application_date","registered_date",
        "date_registered","validated_date","receipt_date",
        "DATEAPRECV","DATEAPVAL","Valid From Date","Registered Date",
    ],
    "decision_date": [
        "decision_date","date_of_decision","determination_date",
        "decision_issued_date","decisiondate",
    ],
    "lat": ["latitude","lat","y_coord","northing"],
    "lng": ["longitude","lng","lon","x_coord","easting"],
}


def find_field(row: dict, key: str) -> Optional[str]:
    candidates = FIELD_MAPS.get(key, [key])
    lookup = {}
    for k, v in row.items():
        lookup[k.lower().strip().replace(" ","_")] = v
        lookup[k.lower().strip()] = v
    for c in candidates:
        v = lookup.get(c.lower().replace(" ","_")) or lookup.get(c.lower())
        if v is not None and str(v).strip() not in ("","None","null","NULL","-"):
            return str(v).strip()
    return None


def parse_csv_content(content: str, council: str, url: str) -> list[dict]:
    apps = []
    try:
        content = content.lstrip("\ufeff")
        first_line = content.split("\n")[0]
        if first_line.count(";") > first_line.count(","):
            delimiter = ";"
        elif first_line.count("\t") > first_line.count(","):
            delimiter = "\t"
        else:
            delimiter = ","

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return []

        cols = list(rows[0].keys())
        print(f"    {len(rows)} rows, delimiter='{delimiter}'")
        print(f"    Cols: {', '.join(cols[:6])}...")

        for row in rows:
            ref = find_field(row, "reference")
            if not ref or len(ref.strip()) < 3:
                continue
            address = find_field(row, "address") or ""
            postcode = find_field(row, "postcode") or _extract_postcode(address)
            submitted = _parse_date(find_field(row, "submitted_date") or "")
            lat = _safe_float(find_field(row, "lat"), "lat")
            lng = _safe_float(find_field(row, "lng"), "lng")
            apps.append({
                "reference": ref.strip(),
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
        print(f"    Parse error: {e}")
    return apps


def geocode_postcodes(postcodes: list[str]) -> dict:
    """Synchronous geocoding via postcodes.io."""
    results = {}
    unique = list({p.strip().upper().replace(" ","") for p in postcodes if p})
    if not unique:
        return results

    import urllib.request
    for i in range(0, len(unique), 100):
        chunk = unique[i:i+100]
        try:
            body = json.dumps({"postcodes": chunk}).encode()
            req = urllib.request.Request(
                "https://api.postcodes.io/postcodes",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                for item in data.get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"]
                        )
        except Exception as e:
            print(f"    Geocode error: {e}")
    return results


def db_upsert(conn, apps: list[dict]) -> tuple[int,int]:
    cur = conn.cursor()
    new = 0
    for app in apps:
        ref = (app.get("reference") or "").strip()
        council_name = (app.get("council_name") or "").strip()
        if not ref or not council_name:
            continue

        # Find council
        cur.execute("SELECT id FROM councils WHERE name ILIKE %s LIMIT 1",
                    (f"%{council_name}%",))
        row = cur.fetchone()
        if not row:
            slug = re.sub(r"[^a-z0-9]+","-",council_name.lower()).strip("-")
            try:
                cur.execute("""
                    INSERT INTO councils (name,slug,system,coverage_source)
                    VALUES (%s,%s,'open_data','data_gov_uk')
                    ON CONFLICT (slug) DO UPDATE
                    SET coverage_source='data_gov_uk',updated_at=NOW()
                    RETURNING id
                """, (council_name, slug))
                conn.commit()
                row = cur.fetchone()
            except Exception:
                conn.rollback()
                continue
        council_id = row[0]

        try:
            cur.execute("""
                INSERT INTO planning_applications
                    (council_id,reference,address,postcode,lat,lng,
                     description,application_type,status,
                     submitted_date,decision_date,council_url,source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (council_id,reference) DO UPDATE SET
                    status=EXCLUDED.status,
                    lat=COALESCE(EXCLUDED.lat,planning_applications.lat),
                    lng=COALESCE(EXCLUDED.lng,planning_applications.lng),
                    updated_at=NOW()
            """, (
                council_id, ref,
                app.get("address"), app.get("postcode"),
                app.get("lat"), app.get("lng"),
                app.get("description"), app.get("application_type"),
                app.get("status","pending"),
                app.get("submitted_date"), app.get("decision_date"),
                app.get("council_url"), app.get("source","data_gov_uk"),
            ))
            if cur.rowcount > 0:
                new += 1
            cur.execute("""
                UPDATE councils SET coverage_source='data_gov_uk',
                last_scraped_at=NOW(),updated_at=NOW() WHERE id=%s
            """, (council_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()

    cur.close()
    return len(apps), new


def main():
    bulk_mode = "--bulk" in sys.argv
    feeds = BULK_FEEDS if bulk_mode else COUNCIL_FEEDS
    mode = "BULK" if bulk_mode else "FAST"

    start = datetime.utcnow()
    print(f"[{start.isoformat()}] PlanPing harvester ({mode} mode)")
    print(f"Connecting to database...")

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=30)
    conn.autocommit = False
    print(f"Connected! Processing {len(feeds)} feeds\n")

    total_apps = total_new = 0

    for council_name, url, fmt in feeds:
        print(f"[{council_name}]")
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                content = r.read().decode("utf-8", errors="replace")
            print(f"  Downloaded {len(content):,} chars")

            if content.lstrip().startswith("<!"):
                print(f"  Got HTML — skipping")
                continue

            apps = parse_csv_content(content, council_name, url)
            print(f"  Parsed {len(apps)} applications")

            if not apps:
                continue

            # Geocode missing postcodes
            need_geo = [a["postcode"] for a in apps
                        if not a.get("lat") and a.get("postcode")]
            if need_geo:
                print(f"  Geocoding {len(set(need_geo))} postcodes...")
                coords = geocode_postcodes(need_geo)
                for app in apps:
                    if not app.get("lat") and app.get("postcode"):
                        pc = app["postcode"].strip().upper().replace(" ","")
                        c = coords.get(pc)
                        if c:
                            app["lat"], app["lng"] = c

            found, new = db_upsert(conn, apps)
            total_apps += found
            total_new += new
            print(f"  ✓ {new} new of {found} saved")

        except Exception as e:
            print(f"  Error: {e}")

    elapsed = (datetime.utcnow() - start).seconds
    print(f"\nDone in {elapsed}s. Total={total_apps}, New={total_new}")
    conn.close()


if __name__ == "__main__":
    main()
