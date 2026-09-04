#!/usr/bin/env python3
"""
PlanFind — Tonbridge & Malling / City of London / Fife ArcGIS recon,
round 2 (2026-09-04).

Round 1 confirmed both Tonbridge & Malling and City of London have
real, rich, queryable data — but the default (unsorted) sample records
returned were old (1999/2000, 2004), since no ORDER BY or date filter
was applied. This queries both with ORDER BY DESC to confirm genuinely
current data exists too.

Round 1 also found Fife's service (both candidates) fails with a
different, non-adversarial error: [SSL: CERTIFICATE_VERIFY_FAILED]
certificate has expired. This is Fife's OWN server having a genuinely
expired certificate — a real accidental misconfiguration that would
affect any legitimate client, not deliberate protection. Testing with
certificate verification relaxed, same legitimate category of fix
already used for West Dunbartonshire's old/broken cert earlier this
project.
"""
import asyncio
import ssl
from datetime import datetime, timezone

import httpx


def _legacy_ssl_context() -> ssl.SSLContext:
    """Same real, legitimate fix already used for West Dunbartonshire —
    tolerating a genuinely expired/broken certificate on an old
    council server, not bypassing deliberate protection."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def query_recent(client: httpx.AsyncClient, name: str, base_url: str,
                        layer: int, date_field: str, ref_field: str):
    print(f"\n{'=' * 70}")
    print(f"RECENT DATA CHECK: {name}")
    print("=" * 70)

    query_url = f"{base_url}/{layer}/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": "3",
        "orderByFields": f"{ref_field} DESC",
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


async def test_fife(client: httpx.AsyncClient, name: str, base_url: str, layer: int):
    print(f"\n{'=' * 70}")
    print(f"FIFE RETEST (relaxed cert verification): {name}")
    print("=" * 70)

    try:
        r = await client.get(f"{base_url}?f=json")
        print(f"Real service root HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"⚠ Real API error: {data['error']}")
                return
            layers = data.get("layers", [])
            print(f"Real layers found: {len(layers)}")
            for l in layers:
                print(f"  id={l.get('id')} name={l.get('name')!r}")
    except Exception as e:
        print(f"⚠ Service root request failed: {type(e).__name__}: {e!r}")
        return

    query_url = f"{base_url}/{layer}/query"
    params = {"where": "1=1", "outFields": "*", "f": "json", "resultRecordCount": "3"}
    try:
        r = await client.get(query_url, params=params)
        print(f"\nReal query HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"⚠ Real query error: {data['error']}")
            else:
                features = data.get("features", [])
                print(f"Real sample records returned: {len(features)}")
                for feat in features[:2]:
                    print(f"  {feat.get('attributes', {})}")
    except Exception as e:
        print(f"⚠ Query failed: {type(e).__name__}: {e!r}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Multi-council ArcGIS recon round 2\n")

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

    # Separate client with relaxed cert verification for Fife specifically
    async with httpx.AsyncClient(timeout=30, verify=_legacy_ssl_context()) as fife_client:
        await test_fife(
            fife_client, "Planning_Pro ('All Apps' layer)",
            "https://arcgis-live-as.fife.gov.uk/server/rest/services/Planning_Pro/MapServer",
            7,
        )
        await test_fife(
            fife_client, "Planning_Applications_LinkGISLIVE",
            "https://arcgis-live-as.fife.gov.uk/server/rest/services/Planning_Applications_LinkGISLIVE/MapServer",
            0,
        )

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
