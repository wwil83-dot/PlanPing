#!/usr/bin/env python3
"""
PlanFind — Glasgow ArcGIS FeatureServer recon, round 7 (2026-09-04).

Round 6's console log capture found the real service directly, logged
by the ArcGIS JS API itself while rendering the dashboard (not caught
by the network request listener — a registered service worker likely
intercepted/cached the actual fetch below Playwright's visibility):

    https://utility.arcgis.com/usrsvcs/servers/
    0d583bf0d00246408fff19fa812bb1b5/rest/services/AGOL/
    Planning_Applications_Major_Significant/MapServer

Referenced at layer indices 1 and 3 specifically, with two logged
warnings ("unable to find field of type 'geometry'", "'objectIdField'
property is not defined") — real but non-fatal metadata quirks on
Glasgow's end, not blockers; the layers are genuinely being queried by
the live dashboard regardless. This queries layers 0-4 directly to
find the real schema and confirm which layers hold real, usable
records.
"""
import asyncio
from datetime import datetime, timezone

import httpx

BASE_URL = (
    "https://utility.arcgis.com/usrsvcs/servers/"
    "0d583bf0d00246408fff19fa812bb1b5/rest/services/AGOL/"
    "Planning_Applications_Major_Significant/MapServer"
)


async def test_layer(client: httpx.AsyncClient, layer_index: int):
    print(f"\n{'=' * 70}")
    print(f"LAYER {layer_index}")
    print("=" * 70)

    # First check the layer's own metadata (name, fields)
    meta_url = f"{BASE_URL}/{layer_index}?f=json"
    try:
        r = await client.get(meta_url)
        print(f"Real metadata HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"⚠ Real API error: {data['error']}")
                return
            print(f"Real layer name: {data.get('name', '')!r}")
            fields = data.get("fields", [])
            print(f"Real fields ({len(fields)}):")
            for f in fields[:20]:
                print(f"  {f.get('name')} ({f.get('type')})")
    except Exception as e:
        print(f"⚠ Metadata request failed: {type(e).__name__}: {e!r}")
        return

    # Then query for a real sample of records
    query_url = f"{BASE_URL}/{layer_index}/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": "3",
        "orderByFields": "OBJECTID DESC",
    }
    try:
        r = await client.get(query_url, params=params)
        print(f"\nReal query HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"⚠ Real query API error: {data['error']}")
            else:
                features = data.get("features", [])
                print(f"Real sample records returned: {len(features)}")
                for feat in features:
                    print(f"  {feat.get('attributes', {})}")
    except Exception as e:
        print(f"⚠ Query request failed: {type(e).__name__}: {e!r}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow MapServer direct query recon\n")
    print(f"Base service: {BASE_URL}\n")

    async with httpx.AsyncClient(timeout=30) as client:
        # Check the service root first for a real layer listing
        try:
            r = await client.get(f"{BASE_URL}?f=json")
            print(f"Real service root HTTP status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                layers = data.get("layers", [])
                print(f"Real layers listed at service root: {len(layers)}")
                for l in layers:
                    print(f"  id={l.get('id')} name={l.get('name')!r}")
        except Exception as e:
            print(f"⚠ Service root request failed: {type(e).__name__}: {e!r}")

        for i in range(5):
            await test_layer(client, i)

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
