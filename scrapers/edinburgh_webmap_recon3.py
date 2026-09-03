#!/usr/bin/env python3
"""
PlanFind — Edinburgh ArcGIS FeatureServer recon, round 3 (2026-09-03).

Round 2's saved config revealed the real structure directly: the Web
AppBuilder app doesn't embed a FeatureServer URL itself — it references
the underlying WEB MAP by item ID instead:
    "map": {"itemId": "af6b177c787b4831b6745ee149cf71fd", ...}

This is a standard, well-documented ArcGIS pattern: a web map item's
own /data JSON contains an "operationalLayers" array, each with a real
"url" field pointing to its FeatureServer/MapServer. This fetches that
web map's config directly and extracts the real layer URLs.
"""
import json
import asyncio
from datetime import datetime, timezone

import httpx

EDINBURGH_ORG_HOST = "cityofedinburgh.maps.arcgis.com"
EDINBURGH_WEBMAP_ITEM_ID = "af6b177c787b4831b6745ee149cf71fd"


async def test_featureserver(client: httpx.AsyncClient, base_url: str):
    print(f"\n  Testing real FeatureServer: {base_url}")
    clean_base = base_url.rstrip("/")
    # If the URL already points at a specific layer (ends in a number),
    # query it directly; otherwise assume layer 0.
    if clean_base.split("/")[-1].isdigit():
        query_url = f"{clean_base}/query"
    else:
        query_url = f"{clean_base}/0/query"
    params = {"where": "1=1", "outFields": "*", "f": "json", "resultRecordCount": "3"}
    try:
        r = await client.get(query_url, params=params)
        print(f"    Real HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"    ⚠ Real API error: {data['error']}")
            else:
                features = data.get("features", [])
                print(f"    Real sample records returned: {len(features)}")
                for feat in features[:3]:
                    print(f"      {feat.get('attributes', {})}")
    except Exception as e:
        print(f"    ⚠ Query failed: {type(e).__name__}: {e!r}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Edinburgh web map recon round 3\n")

    url = f"https://{EDINBURGH_ORG_HOST}/sharing/rest/content/items/{EDINBURGH_WEBMAP_ITEM_ID}/data?f=json"
    print(f"Fetching real web map config: {url}")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(url)
            print(f"Real HTTP status: {r.status_code}")
            if r.status_code != 200:
                print(f"⚠ Non-200. First 500 chars: {r.text[:500]!r}")
                return

            data = r.json()
            with open("/tmp/edinburgh_webmap_config.json", "w") as f:
                json.dump(data, f, indent=2)
            print("Saved: /tmp/edinburgh_webmap_config.json")

            layers = data.get("operationalLayers", [])
            print(f"\nReal operational layers found: {len(layers)}")
            for layer in layers:
                title = layer.get("title", "")
                layer_url = layer.get("url", "")
                print(f"\n  Layer: {title!r}")
                print(f"  Real URL: {layer_url!r}")
                if layer_url:
                    await test_featureserver(client, layer_url)

        except Exception as e:
            print(f"⚠ Request failed: {type(e).__name__}: {e!r}")

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
