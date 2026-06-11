#!/usr/bin/env python3
"""
PlanPing — council open data harvester.
Uses asyncpg with statement_cache_size=0 (required for Supabase pooler).
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

import asyncpg
import httpx

DATABASE_URL = os.environ["DATABASE_URL"]

HEADERS = {
    "User-Agent": "PlanPing/1.0 (+https://planping.onrender.com)",
    "Accept": "text/csv,application/json,*/*",
}

COUNCIL_FEEDS = [
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
    s = str(s).strip()
    if "+" in s:
        s = s.split("+")[0].strip()
    if " " in s:
        s = s.split(" ")[0]
    if "T" in s:
        s = s.split("T")[0]
    s = s[:10]
    for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%d/%m/%y","%Y/%m/%d","%d.%m.%Y"):
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

        MAX_ROWS = 5000
        if len(rows) > MAX_ROWS:
            rows = rows[-MAX_ROWS:]

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


async def geocode_batch(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ","") for p in postcodes if p})
    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(0, len(unique), 100):
            chunk = unique[i:i+100]
            try:
                r = await client.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": chunk}
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"]
                        )
            except Exception as e:
                print(f"    Geocode error: {e}")
            await asyncio.sleep(0.2)
    return results


async def upsert(conn, apps: list[dict]) -> tuple[int,int]:
    new = 0
    for app in apps:
        ref = (app.get("reference") or "").strip()
        council_name = (app.get("council_name") or "").strip()
        if not ref or not council_name:
            continue

        council_id = await conn.fetchval(
            "SELECT id FROM councils WHERE name ILIKE $1 LIMIT 1",
            f"%{council_name}%"
        )
        if not council_id:
            slug = re.sub(r"[^a-z0-9]+","-",council_name.lower()).strip("-")
            try:
                council_id = await conn.fetchval("""
                    INSERT INTO councils (name,slug,system,coverage_source)
                    VALUES ($1,$2,'open_data','data_gov_uk')
                    ON CONFLICT (slug) DO UPDATE
                    SET coverage_source='data_gov_uk',updated_at=NOW()
                    RETURNING id
                """, council_name, slug)
            except Exception:
                continue

        try:
            result = await conn.execute("""
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
            await conn.execute("""
                UPDATE councils SET coverage_source='data_gov_uk',
                last_scraped_at=NOW(),updated_at=NOW() WHERE id=$1
            """, council_id)
        except Exception as e:
            pass

    return len(apps), new


async def main():
    bulk_mode = "--bulk" in sys.argv
    feeds = BULK_FEEDS if bulk_mode else COUNCIL_FEEDS
    mode = "BULK" if bulk_mode else "FAST"

    start = datetime.utcnow()
    print(f"[{start.isoformat()}] PlanPing harvester ({mode} mode)")
    print("Connecting to database...")

    # Use single connection (not pool) — more reliable with Supabase pooler
    conn = await asyncpg.connect(
        DATABASE_URL,
        statement_cache_size=0,
        ssl="require",
        timeout=30,
    )
    print(f"Connected! Processing {len(feeds)} feeds\n")

    total_apps = total_new = 0

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers=HEADERS
    ) as client:
        for council_name, url, fmt in feeds:
            print(f"[{council_name}]")
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    print(f"  HTTP {r.status_code} — skipping")
                    continue

                content = r.text
                print(f"  Downloaded {len(content):,} chars")

                if content.lstrip().startswith("<!"):
                    print(f"  Got HTML — skipping")
                    continue

                apps = parse_csv_content(content, council_name, url)
                print(f"  Parsed {len(apps)}")

                if not apps:
                    continue

                need_geo = [a["postcode"] for a in apps
                            if not a.get("lat") and a.get("postcode")]
                if need_geo:
                    print(f"  Geocoding {len(set(need_geo))} postcodes...")
                    coords = await geocode_batch(need_geo)
                    for app in apps:
                        if not app.get("lat") and app.get("postcode"):
                            pc = app["postcode"].strip().upper().replace(" ","")
                            c = coords.get(pc)
                            if c:
                                app["lat"], app["lng"] = c

                found, new = await upsert(conn, apps)
                total_apps += found
                total_new += new
                print(f"  ✓ {new} new of {found} saved")

            except Exception as e:
                print(f"  Error: {e}")

    elapsed = (datetime.utcnow() - start).seconds
    print(f"\nDone in {elapsed}s. Total={total_apps}, New={total_new}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
