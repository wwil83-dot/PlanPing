"""
Import architects/tradespeople from Companies House's free "Basic
Company Data" bulk product into the `professionals` table.

Real, confirmed source (no API key needed, no signup):
    http://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-YYYY-MM-01.zip
Released monthly (~1st of the month, but the current month's file
isn't always up yet on day 1 — falls back to the previous month).

Real, confirmed CSV structure (55 columns as of Sept 2026):
    CompanyName, CompanyNumber, RegAddress.CareOf, RegAddress.POBox,
    RegAddress.AddressLine1, RegAddress.AddressLine2,
    RegAddress.PostTown, RegAddress.County, RegAddress.Country,
    RegAddress.PostCode, CompanyCategory, CompanyStatus,
    CountryOfOrigin, DissolutionDate, IncorporationDate,
    ... (Accounts/Returns/Mortgages fields, not used here) ...,
    SICCode.SicText_1, SICCode.SicText_2, SICCode.SicText_3,
    SICCode.SicText_4, ... (LimitedPartnerships/PreviousName fields,
    not used here)

REAL, CONFIRMED CAVEAT (Companies House Developer Forum, Sept 2026):
occasional malformed rows exist in the real monthly file (one record
in the September 2026 release parsed to 56 fields against a 55-field
header, likely an unescaped comma in a text field). Every row is
processed defensively — one bad row is skipped and logged, never
allowed to crash the whole import.

Real, confirmed SIC text format: "43210 - Electrical installation" —
matching checks the numeric PREFIX of the SIC text field, not an exact
string match, since the code and description are combined in one
field.

DELIBERATE SCOPE, per real, direct request: this is NOT trying to be
an exhaustive directory. It's a bounded sample — capped at
MAX_PER_TOWN_TRADE per town+trade combination (fewer if that many
don't exist), refreshed once a month. Every public-facing page must
say so explicitly.
"""
import asyncio
import csv
import io
import os
import re
import sys
import zipfile
from datetime import date, datetime, timedelta
from typing import Optional

import asyncpg
import httpx

DOWNLOAD_BASE_URL = "http://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-{date}.zip"
MAX_PER_TOWN_TRADE = int(os.environ.get("MAX_PER_TOWN_TRADE", "20"))

# Real, confirmed UK SIC 2007 codes for each trade category — verified
# directly against multiple current sources before use, not guessed
# from memory alone (see conversation notes). Matched against the
# leading digits of SICCode.SicText_N, since the field combines code
# and description in one string.
TRADE_SIC_CODES: dict[str, str] = {
    "architect":          "71111",
    "general_builder":    "41202",
    "electrician":        "43210",
    "plumber_heating":    "43220",
    "plasterer":          "43310",
    "joiner_carpenter":   "43320",
    "glazier":            "43342",
    "roofer":             "43910",
}
SIC_TO_TRADE = {v: k for k, v in TRADE_SIC_CODES.items()}


def find_real_download_url() -> tuple[str, str]:
    """Companies House publishes on/around the 1st of each month, but
    not always exactly on day 1 — real, confirmed caveat from
    chguide.co.uk. Tries the current month first, falls back to the
    previous month if that's not published yet, rather than assuming
    it always is."""
    today = date.today()
    current_month = today.replace(day=1)
    prev_month = (current_month - timedelta(days=1)).replace(day=1)
    return (
        DOWNLOAD_BASE_URL.format(date=current_month.isoformat()),
        DOWNLOAD_BASE_URL.format(date=prev_month.isoformat()),
    )


def extract_sic_prefix(sic_text: str) -> Optional[str]:
    """Real, confirmed format: '43210 - Electrical installation'.
    Returns just the leading numeric code, or None if the field is
    blank/malformed."""
    if not sic_text:
        return None
    match = re.match(r"^\s*(\d{4,5})", sic_text)
    return match.group(1) if match else None


def match_trade(row: dict) -> Optional[str]:
    """Checks all 4 real SIC text columns for a match against our
    trade list — a company can have more than one SIC code, and we
    only need one to match."""
    for i in range(1, 5):
        sic_text = row.get(f"SICCode.SicText_{i}", "")
        code = extract_sic_prefix(sic_text)
        if code and code in SIC_TO_TRADE:
            return SIC_TO_TRADE[code]
    return None


def parse_incorporation_date(value: str) -> Optional[date]:
    """Real, confirmed format: DD/MM/YYYY."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


async def download_and_extract_csv(client: httpx.AsyncClient) -> io.StringIO:
    current_url, fallback_url = find_real_download_url()

    for url in (current_url, fallback_url):
        print(f"Trying: {url}")
        try:
            r = await client.get(url, timeout=180, follow_redirects=True)
            if r.status_code == 200:
                print(f"  Real download size: {len(r.content) / 1_000_000:.1f} MB")
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
                    print(f"  Real CSV file inside zip: {csv_name}")
                    with z.open(csv_name) as f:
                        return io.StringIO(f.read().decode("utf-8", errors="replace"))
            else:
                print(f"  Real HTTP status: {r.status_code} — trying fallback")
        except Exception as e:
            print(f"  ⚠ Failed: {type(e).__name__}: {e!r} — trying fallback")

    raise RuntimeError("Could not download Companies House data from current or previous month")


def stream_matching_companies(csv_file: io.StringIO) -> list[dict]:
    """Reads the real CSV line-by-line (never loads the whole ~2GB file
    as a single in-memory structure beyond the StringIO buffer itself)
    and yields only real, active companies matching one of our trade
    SIC codes. Defensive against the real, confirmed malformed-row
    issue — a bad row is skipped and logged, never allowed to crash
    the whole import."""
    reader = csv.DictReader(csv_file)
    matched = []
    skipped_malformed = 0
    total_rows = 0

    for row in reader:
        total_rows += 1
        try:
            # REAL FIX — found via direct diagnostic evidence, not
            # assumed: the real Companies House header has inconsistent
            # leading spaces on some fields (confirmed directly:
            # ' CompanyNumber', ' RegAddress.AddressLine2', among
            # others), while others (CompanyName, RegAddress.PostCode)
            # have none. row.get("CompanyNumber") was silently
            # returning empty for every single row, since the real key
            # was " CompanyNumber" — every one of 22,089 computed
            # records upserted against the same blank unique key,
            # collapsing into a single database row on a real live
            # run. Stripping every key immediately, rather than
            # patching CompanyNumber alone, protects every field
            # against this same class of issue, not just the one we
            # happened to notice.
            row = {(k.strip() if k else k): v for k, v in row.items()}

            # REAL FIX — found via direct testing, not assumed: Python's
            # csv.DictReader does NOT raise an exception for a row with
            # MORE fields than the header (the real, confirmed
            # malformed-row scenario from the Companies House forum
            # report). It silently stores the overflow under a literal
            # `None` key and shifts every subsequent field in that row
            # — meaning the try/except below never actually triggers
            # for this case, and a shifted field could coincidentally
            # look valid (e.g. a shifted value might accidentally equal
            # "Active"), silently inserting wrong data. Checking for
            # this key explicitly is the correct, direct way to catch
            # it, confirmed against the exact real scenario reported.
            if None in row:
                skipped_malformed += 1
                continue

            if row.get("CompanyStatus", "").strip() != "Active":
                continue

            trade = match_trade(row)
            if not trade:
                continue

            postcode = (row.get("RegAddress.PostCode") or "").strip()
            if not postcode:
                continue  # can't geocode or town-match without one

            matched.append({
                "company_number": row.get("CompanyNumber", "").strip(),
                "company_name": row.get("CompanyName", "").strip(),
                "trade_category": trade,
                "sic_code": TRADE_SIC_CODES[trade],
                "postcode": postcode,
                "address": ", ".join(p for p in [
                    row.get("RegAddress.AddressLine1", ""),
                    row.get("RegAddress.AddressLine2", ""),
                    row.get("RegAddress.PostTown", ""),
                ] if p.strip()),
                "incorporated_date": parse_incorporation_date(row.get("IncorporationDate", "")),
            })
        except Exception:
            # REAL, CONFIRMED — occasional malformed rows exist in the
            # real monthly file. One bad row must never crash the
            # whole import.
            skipped_malformed += 1
            continue

    print(f"Real total rows processed: {total_rows}")
    print(f"Real rows skipped as malformed: {skipped_malformed}")
    print(f"Real active companies matching a tracked trade: {len(matched)}")
    return matched


async def geocode_postcodes(postcodes: list[str]) -> dict:
    """Reuses the exact same postcodes.io batch pattern already used
    throughout this project (geocode.py / import_towns.py), including
    admin_district for town matching."""
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(unique), 100):
            batch = unique[i:i + 100]
            try:
                r = await client.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": batch},
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = {
                            "lat": item["result"]["latitude"],
                            "lng": item["result"]["longitude"],
                            "admin_district": item["result"].get("admin_district"),
                        }
            except Exception as e:
                print(f"    ⚠ Geocoding batch failed ({len(batch)} postcodes): {e}")
            if (i // 100) % 10 == 0:
                print(f"  Geocoded {min(i + 100, len(unique))}/{len(unique)} postcodes...")
    return results


async def find_nearest_towns_batch(conn: asyncpg.Connection, keys: list[str],
                                     lats: list[float], lngs: list[float]) -> dict[str, int]:
    """REAL FIX — a real live run showed this taking a very long time
    (20,000/150,218 after a substantial wait). The likely dominant cost
    isn't the actual computation, it's NETWORK ROUND-TRIP LATENCY: the
    original version did one separate query per coordinate, each
    paying a real ~50-150ms round-trip cost to Supabase regardless of
    how fast the query itself ran — 150,218 of those adds up to hours
    of pure waiting, not computation. This batches many coordinates
    into a single query via a LATERAL JOIN (one nearest-town lookup
    per input row, but all computed within ONE round-trip per batch),
    paying that network cost once per few thousand coordinates instead
    of once per coordinate."""
    rows = await conn.fetch("""
        WITH input AS (
            SELECT * FROM unnest($1::text[], $2::float8[], $3::float8[])
                AS t(postcode_key, lat, lng)
        )
        SELECT i.postcode_key, nearest.id AS town_id
        FROM input i
        CROSS JOIN LATERAL (
            SELECT id FROM towns
            ORDER BY (
                6371 * acos(
                    LEAST(1.0, GREATEST(-1.0,
                        cos(radians(i.lat)) * cos(radians(towns.lat)) * cos(radians(towns.lng) - radians(i.lng)) +
                        sin(radians(i.lat)) * sin(radians(towns.lat))
                    ))
                )
            ) ASC
            LIMIT 1
        ) AS nearest
    """, keys, lats, lngs)
    return {r["postcode_key"]: r["town_id"] for r in rows}


async def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)

    async with httpx.AsyncClient() as client:
        csv_file = await download_and_extract_csv(client)

    matched = stream_matching_companies(csv_file)
    if not matched:
        print("No matching companies found — nothing to import.")
        return

    print(f"\nGeocoding {len(matched)} real postcodes...")
    coords = await geocode_postcodes([m["postcode"] for m in matched])

    conn = await asyncpg.connect(dsn=dsn, ssl="require", statement_cache_size=0)

    # REAL FIX — batched into groups of 2000 rather than one query per
    # coordinate, eliminating per-coordinate network round-trip
    # overhead (the real, confirmed bottleneck from a live run that was
    # taking hours). Cached by postcode too — many companies genuinely
    # share the same registered address (e.g. the same accountant's
    # office), so this also avoids redundant work for those.
    print(f"\nFinding nearest real town for {len(coords)} unique real coordinates (batched)...")
    postcode_keys = list(coords.keys())
    postcode_to_town: dict[str, int] = {}
    BATCH_SIZE = 2000
    for i in range(0, len(postcode_keys), BATCH_SIZE):
        batch_keys = postcode_keys[i:i + BATCH_SIZE]
        batch_lats = [coords[k]["lat"] for k in batch_keys]
        batch_lngs = [coords[k]["lng"] for k in batch_keys]
        batch_result = await find_nearest_towns_batch(conn, batch_keys, batch_lats, batch_lngs)
        postcode_to_town.update(batch_result)
        print(f"  Matched {min(i + BATCH_SIZE, len(postcode_keys))}/{len(postcode_keys)}...")

    matched_count = len(postcode_to_town)
    print(f"Real coordinates successfully matched to a nearest town: {matched_count}/{len(coords)}")

    # Attach real geocoding + town match to each company
    enriched = []
    for m in matched:
        key = m["postcode"].upper().replace(" ", "")
        geo = coords.get(key)
        if not geo:
            continue  # real postcode that postcodes.io couldn't resolve — skip, don't guess
        town_id = postcode_to_town.get(key)
        if not town_id:
            continue  # should be rare now (every UK coordinate has SOME nearest town), but stay defensive
        m["lat"] = geo["lat"]
        m["lng"] = geo["lng"]
        m["town_id"] = town_id
        enriched.append(m)

    print(f"Real companies with a full town match: {len(enriched)}")

    # DELIBERATE SCOPE, per real, direct request — cap per town+trade,
    # preferring more established companies (older incorporation date)
    # as a simple, defensible proxy for "a real, stably trading
    # business" when Companies House data alone gives no other quality
    # signal (no reviews, no verified activity).
    enriched.sort(key=lambda m: m["incorporated_date"] or date.max)
    capped: dict[tuple, list[dict]] = {}
    for m in enriched:
        key = (m["town_id"], m["trade_category"])
        capped.setdefault(key, [])
        if len(capped[key]) < MAX_PER_TOWN_TRADE:
            capped[key].append(m)

    final_records = [m for group in capped.values() for m in group]
    print(f"Real final records after capping at {MAX_PER_TOWN_TRADE} per town+trade: {len(final_records)}")

    await conn.executemany("""
        INSERT INTO professionals
            (company_number, company_name, trade_category, sic_code,
             postcode, address, lat, lng, town_id, incorporated_date)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (company_number) DO UPDATE SET
            company_name = EXCLUDED.company_name,
            trade_category = EXCLUDED.trade_category,
            sic_code = EXCLUDED.sic_code,
            postcode = EXCLUDED.postcode,
            address = EXCLUDED.address,
            lat = EXCLUDED.lat,
            lng = EXCLUDED.lng,
            town_id = EXCLUDED.town_id,
            incorporated_date = EXCLUDED.incorporated_date,
            last_synced_at = now()
    """, [
        (m["company_number"], m["company_name"], m["trade_category"], m["sic_code"],
         m["postcode"], m["address"], m["lat"], m["lng"], m["town_id"], m["incorporated_date"])
        for m in final_records
    ])

    total = await conn.fetchval("SELECT COUNT(*) FROM professionals")
    print(f"\nDone. Real total rows in professionals table now: {total}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
