#!/usr/bin/env python3
"""
PlanPing harvester — saves to JSON file, uploads to Supabase REST API.
Bypasses asyncpg connection issues by using Supabase HTTP API instead.
"""
import asyncio, csv, io, json, os, re, sys
from datetime import date, datetime
from typing import Optional
import httpx

# Supabase REST API — no TCP connection issues
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HEADERS = {"User-Agent": "PlanPing/1.0 (+https://planfind.co.uk)"}

COUNCIL_FEEDS = [
    # Camden — limited to 2000 most recent
    ("Camden LBC",
     "https://opendata.camden.gov.uk/resource/2eiu-s2cw.csv?$limit=2000&$order=registered_date+DESC",
     "csv"),

    # Wigan 2021-2030 — direct GeoJSON API
    ("Wigan Metropolitan Borough Council",
     "https://maps.wigan.gov.uk/arcgis/rest/services/Planning_BuildingControl/Planning_Applications_2021_to_2030/MapServer/6/query?outFields=*&where=1%3D1&f=geojson",
     "geojson"),

    # City of York — live ArcGIS CSV, last 12 months, includes lat/lng
    ("City of York Council",
     "https://data-cyc.opendata.arcgis.com/datasets/7044d1920639460da3fc4a3fa9273107_5.csv",
     "csv"),
]

BULK_FEEDS = [
    ("Camden LBC",
     "https://opendata.camden.gov.uk/api/views/2eiu-s2cw/rows.csv?accessType=DOWNLOAD",
     "csv"),
    ("Canterbury City Council",
     "https://spatialdata-cbmdc.hub.arcgis.com/api/download/v1/items/eeb3ad1f520a45eea580506c8f097f3f/csv?layers=0",
     "csv"),
    # Wigan historic years
    ("Wigan Metropolitan Borough Council",
     "https://opendata.wigan.gov.uk/api/download/v1/items/2c2afd8ff5c74eeab248a0a0909e0b62/csv?layers=8",
     "csv"),
    # City of York — same feed as nightly (covers last 12 months)
    ("City of York Council",
     "https://data-cyc.opendata.arcgis.com/datasets/7044d1920639460da3fc4a3fa9273107_5.csv",
     "csv"),
]

FIELD_MAPS = {
    "reference": ["application_reference","reference","app_ref","case_reference",
        "planning_reference","ref","application_number","appref","applicationreference",
        "case_ref","application_no","app_no","appl_ref","reference_number","casereference",
        "REFVAL","KEYVAL","Application Number","pk","refval","appno","app_number"],
    "address": ["development_address","site_address","location","site_location",
        "property_address","siteaddress","development_location","full_address","premise",
        "address_of_proposal","Development Address","development address",
        "site address","SITEADDR","site_addr","location_description","Location",
        "LOCATION","LOCDESC","site_description","SITEDESCRIPTION",
        "address_description","ADDRDESC","ADDRESS","address","site_name"],
    "postcode": ["postcode","post_code","site_postcode","development_postcode",
        "POSTCODE","site_post_code","post code","PostCode"],
    "description": ["development_description","description","proposal","development_proposal",
        "application_description","proposed_development","Development Description",
        "development description","proposal_description","work_description",
        "DESCR","development_descr","app_description","PROPOSA","proposal_text",
        "PROPOSAL","development_description","app_proposal"],
    "application_type": ["application_type","app_type","type","application_category",
        "type_of_application","applicationtype","case_type","app type",
        "development_type","Application Type","APPTYPE","apptype","app_cat"],
    "status": ["decision","status","application_status","outcome","current_status",
        "decision_type","determination","DCSTAT","DECSN","Decision Type","APPLDECTYP",
        "app_status","decision_description"],
    "submitted_date": ["date_received","received_date","date_valid","valid_date",
        "submission_date","date_submitted","received","datereceived","date_of_application",
        "application_date","registered_date","date_registered","validated_date","receipt_date",
        "DATEAPRECV","DATEAPVAL","Valid From Date","Registered Date","DATEAPPDEC","DATEDECISN",
        "date_validated","valid_from","reg_date","date_received_valid"],
    "decision_date": ["decision_date","date_of_decision","determination_date",
        "decision_issued_date","decisiondate","date_decision","decision_made_date"],
    "lat": ["latitude","lat","y_coord","northing","x","y"],
    "lng": ["longitude","lng","lon","x_coord","easting","x","long"],
}


def find_field(row, key):
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


def _extract_postcode(text):
    if not text: return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _normalise(s):
    s = (s or "").lower()
    if any(x in s for x in ("approv","grant","permit","allow")): return "approved"
    if any(x in s for x in ("refus","reject","dismiss")): return "refused"
    if "withdraw" in s: return "withdrawn"
    return "pending"


def _parse_date(s):
    if not s: return None
    s = str(s).strip()
    if "+" in s: s = s.split("+")[0].strip()
    if " " in s: s = s.split(" ")[0]
    if "T" in s: s = s.split("T")[0]
    s = s[:10]
    for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%d/%m/%y","%Y/%m/%d","%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _bng_to_wgs84(easting, northing):
    """Approximate British National Grid to WGS84 conversion."""
    lat = 49.766 + northing / 111320
    lng = -7.557 + easting / (111320 * 0.6165)
    if 49 < lat < 61 and -8 < lng < 2:
        return round(lat, 6), round(lng, 6)
    return None, None


def _is_wgs84(x, y):
    """Check if coordinates look like WGS84 lat/lng rather than BNG."""
    return 49 < y < 62 and -9 < x < 3


# Known council portal URL patterns
COUNCIL_PORTAL_URLS = {
    "wigan": "https://planning.wigan.gov.uk/online-applications/",
    "camden": "https://planningrecords.camden.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
    "canterbury": "https://pa.canterbury.gov.uk/online-applications/",
    "south lakeland": "https://www.westmorlandandfurness.gov.uk/planning-and-building-control/planning/search-planning-application",
    "york": "https://www.york.gov.uk/SearchPlanningApplications",
}


def _build_council_url(props: dict, council: str, reference: str) -> str:
    """Get the best URL for this application — prefer PA_LINK, fall back to portal search."""
    pa_link = find_field(props, "council_url_field")
    if pa_link and pa_link.startswith("http") and "arcgis" not in pa_link and "wigan.gov.uk/arcgis" not in pa_link:
        return pa_link
    return _build_portal_url(council, reference)


def _build_portal_url(council: str, reference: str) -> str:
    """Build a council portal search URL for a given reference."""
    council_lower = council.lower()
    for key, url in COUNCIL_PORTAL_URLS.items():
        if key in council_lower:
            return url
    return ""


def parse_geojson(content, council, url):
    apps = []
    try:
        data = json.loads(content)
        features = data.get("features", data if isinstance(data, list) else [])

        if features:
            sample = features[0].get("properties", {}) if isinstance(features[0], dict) else {}
            cols = list(sample.keys())
            print(f"    GeoJSON props: {', '.join(cols[:8])}{'...' if len(cols)>8 else ''}")

        for f in features:
            if not isinstance(f, dict):
                continue
            props = f.get("properties", {}) or {}
            geom = f.get("geometry", {}) or {}

            ref = find_field(props, "reference")
            if not ref or len(ref.strip()) < 3:
                continue

            lat = lng = None
            if geom.get("type") == "Point":
                coords = geom.get("coordinates", [])
                if len(coords) >= 2:
                    x, y = coords[0], coords[1]
                    if _is_wgs84(x, y):
                        lng, lat = x, y
                    elif 100000 < x < 700000 and 0 < y < 1300000:
                        lat, lng = _bng_to_wgs84(x, y)

            address = find_field(props, "address") or ""
            if "\r" in address or address.count("\n") > 1:
                lines = [l.strip() for l in address.replace("\r","\n").split("\n") if l.strip()]
                if lines:
                    address = ", ".join(lines[:3])

            postcode = find_field(props, "postcode") or _extract_postcode(address)

            apps.append({
                "reference": ref.strip(),
                "address": address,
                "postcode": postcode,
                "lat": lat, "lng": lng,
                "description": find_field(props, "description") or "",
                "application_type": find_field(props, "application_type") or "",
                "status": _normalise(find_field(props, "status") or ""),
                "submitted_date": _parse_date(find_field(props, "submitted_date") or ""),
                "decision_date": _parse_date(find_field(props, "decision_date") or ""),
                "council_name": council,
                "council_url": _build_council_url(props, council, ref.strip()),
                "source": "data_gov_uk",
            })
    except Exception as e:
        print(f"    GeoJSON error: {e}")
    return apps


def parse_csv(content, council, url):
    apps = []
    try:
        content = content.lstrip("\ufeff")
        first_line = content.split("\n")[0]
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        rows = list(reader)
        if not rows: return []
        print(f"    {len(rows)} rows")

        if rows:
            addr_attempt = find_field(rows[0], "address")
            desc_attempt = find_field(rows[0], "description")
            pc_attempt = find_field(rows[0], "postcode")
            lat_attempt = find_field(rows[0], "lat")
            print(f"    Sample - addr: {addr_attempt!r}, desc: {str(desc_attempt)[:30]!r}, pc: {pc_attempt!r}, lat: {lat_attempt!r}")

        for row in rows:
            ref = find_field(row, "reference")
            if not ref or len(ref.strip()) < 3: continue

            address = find_field(row, "address") or ""
            if "\r" in address or address.count("\n") > 1:
                lines = [l.strip() for l in address.replace("\r","\n").split("\n") if l.strip()]
                if lines:
                    address = ", ".join(lines[:3])

            # Extract lat/lng from CSV if present
            lat = lng = None
            raw_lat = find_field(row, "lat")
            raw_lng = find_field(row, "lng")
            if raw_lat and raw_lng:
                try:
                    flat, flng = float(raw_lat), float(raw_lng)
                    if _is_wgs84(flng, flat):
                        lat, lng = flat, flng
                    elif 100000 < flat < 700000 and 0 < flng < 1300000:
                        lat, lng = _bng_to_wgs84(flat, flng)
                except (ValueError, TypeError):
                    pass

            portal_url = find_field(row, "council_url_field") or _build_portal_url(council, ref)
            apps.append({
                "reference": ref.strip(),
                "address": address,
                "postcode": find_field(row, "postcode") or _extract_postcode(address),
                "lat": lat,
                "lng": lng,
                "description": find_field(row, "description") or "",
                "application_type": find_field(row, "application_type") or "",
                "status": _normalise(find_field(row, "status") or ""),
                "submitted_date": _parse_date(find_field(row, "submitted_date") or ""),
                "decision_date": _parse_date(find_field(row, "decision_date") or ""),
                "council_name": council,
                "council_url": portal_url,
                "source": "data_gov_uk",
            })
    except Exception as e:
        print(f"    Parse error: {e}")
    return apps


async def geocode(postcodes):
    results = {}
    unique = list({p.strip().upper().replace(" ","") for p in postcodes if p})
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(unique), 100):
            try:
                r = await c.post("https://api.postcodes.io/postcodes",
                                 json={"postcodes": unique[i:i+100]})
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"])
            except: pass
            await asyncio.sleep(0.2)
    return results


async def supabase_upsert(apps, council_name):
    """Use Supabase REST API to upsert — no TCP connection needed."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  No SUPABASE_URL/KEY — saving to JSON file instead")
        with open(f"output_{council_name.replace(' ','_')}.json","w") as f:
            json.dump(apps, f, default=str)
        print(f"  Saved {len(apps)} to JSON")
        return len(apps), len(apps)

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    # First find council_id
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/councils",
            params={"name": f"ilike.*{council_name.split(' Council')[0]}*",
                    "select": "id"},
            headers=headers
        )
        councils = r.json()
        if not councils:
            print(f"  Council not found in DB: {council_name}")
            return 0, 0
        council_id = councils[0]["id"]

    # Batch upsert
    batch_size = 100
    new = 0
    async with httpx.AsyncClient(timeout=30) as c:
        for i in range(0, len(apps), batch_size):
            batch = apps[i:i+batch_size]
            records = []
            for app in batch:
                records.append({
                    "council_id": council_id,
                    "reference": app["reference"],
                    "address": app.get("address"),
                    "postcode": app.get("postcode"),
                    "lat": app.get("lat"),
                    "lng": app.get("lng"),
                    "description": app.get("description"),
                    "application_type": app.get("application_type"),
                    "status": app.get("status","pending"),
                    "submitted_date": app.get("submitted_date"),
                    "decision_date": app.get("decision_date"),
                    "council_url": app.get("council_url"),
                    "source": app.get("source","data_gov_uk"),
                })
            try:
                r = await c.post(
                    f"{SUPABASE_URL}/rest/v1/planning_applications",
                    json=records,
                    headers=headers
                )
                if r.status_code in (200,201):
                    new += len(batch)
            except Exception as e:
                print(f"  Batch error: {e}")

    # Mark council as covered in the DB so the UI shows the green banner
    if new > 0:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.patch(
                    f"{SUPABASE_URL}/rest/v1/councils",
                    params={"id": f"eq.{council_id}"},
                    json={"coverage_source": "data_gov_uk", "last_scraped_at": datetime.utcnow().isoformat()},
                    headers={**headers, "Prefer": "return=minimal"},
                )
        except Exception as e:
            print(f"  Warning: couldn't update coverage_source: {e}")

    return len(apps), new


async def main():
    bulk = "--bulk" in sys.argv
    feeds = BULK_FEEDS if bulk else COUNCIL_FEEDS
    print(f"[{datetime.utcnow().isoformat()}] PlanPing ({'BULK' if bulk else 'FAST'} mode)")
    print(f"Using Supabase REST API (no TCP connection)")
    print(f"SUPABASE_URL: {'set' if SUPABASE_URL else 'NOT SET'}")
    print(f"SUPABASE_KEY: {'set' if SUPABASE_KEY else 'NOT SET'}\n")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=HEADERS) as c:
        for council_name, url, fmt in feeds:
            print(f"[{council_name}]")
            try:
                r = await c.get(url)
                if r.status_code != 200:
                    print(f"  HTTP {r.status_code}"); continue
                content = r.text
                print(f"  Downloaded {len(content):,} chars")
                if content.lstrip().startswith("<!"):
                    print("  Got HTML"); continue

                if fmt == "geojson" or content.lstrip().startswith("{") or content.lstrip().startswith("["):
                    apps = parse_geojson(content, council_name, url)
                else:
                    apps = parse_csv(content, council_name, url)
                print(f"  Parsed {len(apps)}")
                if not apps: continue

                need = [a["postcode"] for a in apps if not a.get("lat") and a.get("postcode")]
                if need:
                    print(f"  Geocoding {len(set(need))} postcodes...")
                    coords = await geocode(need)
                    for app in apps:
                        if not app.get("lat") and app.get("postcode"):
                            pc = app["postcode"].strip().upper().replace(" ","")
                            co = coords.get(pc)
                            if co: app["lat"], app["lng"] = co

                found, new = await supabase_upsert(apps, council_name)
                print(f"  ✓ {new} new of {found}")
            except Exception as e:
                print(f"  Error: {e}")

    print(f"\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
