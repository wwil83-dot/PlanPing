#!/usr/bin/env python3
"""
PlanFind — Glasgow ArcGIS FeatureServer recon, round 4 (2026-09-03).

Round 1 fetched Glasgow's Experience Builder config from the generic
public www.arcgis.com endpoint and got back a config with a completely
empty "dataSources" object, empty "views", and zero literal
FeatureServer/MapServer references anywhere — genuinely nothing to
resolve. But the SAME config file revealed the real org host directly:
"portalUrl": "https://GlasgowGIS.maps.arcgis.com" — meaning this app is
hosted under Glasgow's own ArcGIS organization, not the generic public
one. Edinburgh needed exactly this same org-specific host to get a
complete config; Glasgow's round 1 fetch used the wrong host entirely.

This re-fetches from the real org host and searches thoroughly for any
FeatureServer/MapServer references or webMap/itemId cross-references.
"""
import json
import re
import asyncio
from datetime import datetime, timezone

import httpx

GLASGOW_ORG_HOST = "glasgowgis.maps.arcgis.com"
GLASGOW_EXPERIENCE_ITEM_ID = "158560dc6db447cc9eeb4a40ca8c1e79"


def find_urls_in_json(obj, found=None):
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for v in obj.values():
            find_urls_in_json(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_urls_in_json(v, found)
    elif isinstance(obj, str):
        if re.search(r"(FeatureServer|MapServer)", obj, re.IGNORECASE):
            found.add(obj)
    return found


def find_item_id_refs(obj, found=None):
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ("itemid", "datasourceitemid", "webmap", "webmapid") and isinstance(v, str) and len(v) >= 30:
                found.add(v)
            find_item_id_refs(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_item_id_refs(v, found)
    return found


async def test_featureserver(client: httpx.AsyncClient, base_url: str):
    print(f"\n  Testing real FeatureServer: {base_url}")
    clean_base = base_url.split("?")[0].rstrip("/")
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


async def resolve_item(client: httpx.AsyncClient, item_id: str, org_host: str):
    lookup_url = f"https://{org_host}/sharing/rest/content/items/{item_id}?f=json"
    try:
        r = await client.get(lookup_url)
        if r.status_code == 200:
            item_data = r.json()
            real_url = item_data.get("url", "")
            title = item_data.get("title", "")
            item_type = item_data.get("type", "")
            print(f"\n  Resolved {item_id}:")
            print(f"    Title: {title!r}, Type: {item_type!r}")
            print(f"    Real URL: {real_url!r}")
            if real_url and re.search(r"(FeatureServer|MapServer)", real_url, re.IGNORECASE):
                await test_featureserver(client, real_url)
            elif item_type == "Web Map":
                # A web map itself — fetch ITS /data for operationalLayers
                data_url = f"https://{org_host}/sharing/rest/content/items/{item_id}/data?f=json"
                r2 = await client.get(data_url)
                if r2.status_code == 200:
                    map_data = r2.json()
                    layers = map_data.get("operationalLayers", [])
                    print(f"    Real operational layers in this web map: {len(layers)}")
                    for layer in layers:
                        layer_url = layer.get("url", "")
                        print(f"      Layer {layer.get('title', '')!r}: {layer_url!r}")
                        if layer_url:
                            await test_featureserver(client, layer_url)
    except Exception as e:
        print(f"    ⚠ Lookup failed for {item_id}: {e}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow ArcGIS recon round 4 "
          f"(correct org host: {GLASGOW_ORG_HOST})\n")

    url = f"https://{GLASGOW_ORG_HOST}/sharing/rest/content/items/{GLASGOW_EXPERIENCE_ITEM_ID}/data?f=json"
    print(f"Fetching from real org host: {url}")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(url)
            print(f"Real HTTP status: {r.status_code}")
            if r.status_code != 200:
                print(f"⚠ Non-200. First 500 chars: {r.text[:500]!r}")
                return

            data = r.json()
            with open("/tmp/glasgow_experience_config_orghost.json", "w") as f:
                json.dump(data, f, indent=2)
            print("Saved: /tmp/glasgow_experience_config_orghost.json")

            found_urls = find_urls_in_json(data)
            print(f"\nReal FeatureServer/MapServer URLs found directly: {len(found_urls)}")
            for u in found_urls:
                print(f"  {u}")
                await test_featureserver(client, u)

            item_refs = find_item_id_refs(data)
            print(f"\nReal item/webMap ID references found: {len(item_refs)}")
            for iid in item_refs:
                print(f"  {iid}")
                await resolve_item(client, iid, GLASGOW_ORG_HOST)

            if not found_urls and not item_refs:
                print("\n⚠ Still nothing found even from the correct org host.")
                print("Real dataSources key:", json.dumps(data.get("dataSources", "MISSING"))[:200])
                print("Real widgets count:", len(data.get("widgets", {})))

        except Exception as e:
            print(f"⚠ Request failed: {type(e).__name__}: {e!r}")

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
