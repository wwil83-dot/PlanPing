#!/usr/bin/env python3
"""
PlanFind — Tonbridge & Malling / City of London recent-data recon,
round 3 (2026-09-04).

Round 2 sorted by the reference field (text) to try to confirm recent
data exists for these two councils, but that doesn't correlate with
recency at all — a literal "TEST" record and odd "R10(2)"/"BC-5170L"
references sorted first purely alphabetically. This sorts by the real
DATE field directly instead, the only reliable way to confirm
genuinely recent (2026) data exists.

(Fife was separately fully confirmed working in round 2 — real,
genuinely fresh data with DATE_UPLOADED timestamps from the day before
testing — see fife_councils.py / fife_scraper.py, already built.)
"""
import asyncio
from datetime import datetime, timezone

import httpx


async def query_recent(client: httpx.AsyncClient, name: str, base_url: str,
                        layer: int, date_field: str, ref_field: str):
    print(f"\n{'=' * 70}")
    print(f"RECENT DATA CHECK: {name}")
    print("=" * 70)

    query_url = f"{base_url}/{layer}/query"
    params = {
        # REAL FIX (2026-09-04), round 4 — City of London's round 3
        # result showed EVERY "most recent" record with a NULL date
        # field, floating to the top of the DESC sort (a common
        # database behaviour, not proof recent data doesn't exist).
        # Explicitly excluding nulls is the only way to get a
        # meaningful answer.
        "where": f"{date_field} IS NOT NULL",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": "5",
        "orderByFields": f"{date_field} DESC",
    }
    try:
        r = await client.get(query_url, params=params)
        print(f"Real query HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"⚠ Real query error: {data['error']}")
            else:
                features = data.get("features", [])
                print(f"Real sample records returned: {len(features)}")
                for feat in features:
                    attrs = feat.get("attributes", {})
                    date_val = attrs.get(date_field)
                    if isinstance(date_val, (int, float)) and date_val > 0:
                        real_date = datetime.fromtimestamp(date_val / 1000, tz=timezone.utc).date().isoformat()
                    else:
                        real_date = date_val
                    print(f"  ref={attrs.get(ref_field)!r}, {date_field}={real_date!r}")
    except Exception as e:
        print(f"⚠ Query failed: {type(e).__name__}: {e!r}")


async def retest_original_query(client: httpx.AsyncClient, name: str, base_url: str, layer: int):
    """Real, differential test: this exact query (no WHERE filter, no
    sort) worked fine in round 1. Retrying it NOW isolates whether the
    WHERE-clause-filtered query specifically triggered the 'Token
    Required' error, or whether something else (rate-limit, session
    timing) unrelated to query shape did."""
    print(f"\n{'=' * 70}")
    print(f"DIFFERENTIAL RETEST (original round-1 query, unchanged): {name}")
    print("=" * 70)

    query_url = f"{base_url}/{layer}/query"
    params = {"where": "1=1", "outFields": "*", "f": "json", "resultRecordCount": "3"}
    try:
        r = await client.get(query_url, params=params)
        print(f"Real query HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"⚠ Real query error: {data['error']}")
                print("  -> If this ALSO now fails, the trigger is unrelated to "
                      "query shape (rate-limit/session timing). If it still "
                      "WORKS, the WHERE clause specifically was the trigger.")
            else:
                features = data.get("features", [])
                print(f"Real sample records returned: {len(features)}")
                print("  -> This still working confirms the WHERE clause "
                      "specifically triggered the token requirement, not "
                      "something time/rate-based.")
    except Exception as e:
        print(f"⚠ Query failed: {type(e).__name__}: {e!r}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Tonbridge/City of London recent-data recon (round 3)\n")

    async with httpx.AsyncClient(timeout=30) as client:
        await query_recent(
            client, "Tonbridge and Malling Borough Council",
            "https://mapsat.tmbc.gov.uk/server/rest/services/Agile_Maps/Planning_Applications_dashboard/MapServer",
            0, "dateapreceived", "reference",
        )
        await query_recent(
            client, "City of London Corporation",
            "https://www.mapping.cityoflondon.gov.uk/arcgis/rest/services/COMPASS_Planning_Planning_Applications/MapServer",
            0, "DATEAPPVAL", "REFVAL",
        )
        await retest_original_query(
            client, "City of London Corporation",
            "https://www.mapping.cityoflondon.gov.uk/arcgis/rest/services/COMPASS_Planning_Planning_Applications/MapServer",
            0,
        )

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
