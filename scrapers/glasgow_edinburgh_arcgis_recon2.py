#!/usr/bin/env python3
"""
PlanFind — Glasgow/Edinburgh ArcGIS FeatureServer recon, round 2
(2026-09-03).

Round 1 found zero literal FeatureServer/MapServer URLs in Glasgow's
Experience Builder config — a known quirk of that framework, which
often references its real data sources by an internal ArcGIS item ID
rather than embedding the literal service URL directly. This resolves
any itemId references found in the config via a follow-up lookup.

Round 1 also found Edinburgh's "applications received and decided map"
uses the OLDER, simpler "Web AppBuilder" framework instead
(cityofedinburgh.maps.arcgis.com/apps/webappviewer/...) — an org-hosted
app, so its config needs to be fetched via THAT org's own sharing/rest
endpoint, not the generic public www.arcgis.com one. Web AppBuilder's
config format is much more likely to embed literal service URLs
directly, being an older/simpler framework than Experience Builder.
"""
import asyncio
import json
import re
from datetime import datetime, timezone

import httpx

GLASGOW_EXPERIENCE_ITEM_ID = "158560dc6db447cc9eeb4a40ca8c1e79"
EDINBURGH_WEBAPPVIEWER_ITEM_ID = "0a03789260954f0dbcbc8b124003d91b"
EDINBURGH_ORG_HOST = "cityofedinburgh.maps.arcgis.com"


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
    """Experience Builder commonly references data sources as
    {"itemId": "..."} rather than a literal URL — find all such
    references so each can be resolved with a follow-up lookup."""
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("itemId", "dataSourceItemId") and isinstance(v, str) and len(v) == 32:
                found.add(v)
            find_item_id_refs(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_item_id_refs(v, found)
    return found


async def test_featureserver(client: httpx.AsyncClient, base_url: str):
    print(f"\n  Testing real FeatureServer: {base_url}")
    clean_base = base_url.split("?")[0].rstrip("/")
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
                for feat in features[:2]:
                    print(f"      {feat.get('attributes', {})}")
    except Exception as e:
        print(f"    ⚠ Query failed: {type(e).__name__}: {e!r}")


async def recon_glasgow_round2():
    print(f"\n{'=' * 70}")
    print("ROUND 2: Glasgow — resolving Experience Builder itemId references")
    print("=" * 70)

    async with httpx.AsyncClient(timeout=30) as client:
        url = f"https://www.arcgis.com/sharing/rest/content/items/{GLASGOW_EXPERIENCE_ITEM_ID}/data?f=json"
        try:
            r = await client.get(url)
            data = r.json()
        except Exception as e:
            print(f"  ⚠ Could not re-fetch config: {e}")
            return

        item_ids = find_item_id_refs(data)
        print(f"  Real itemId references found in config: {len(item_ids)}")
        for iid in item_ids:
            print(f"    {iid}")

        for iid in item_ids:
            lookup_url = f"https://www.arcgis.com/sharing/rest/content/items/{iid}?f=json"
            try:
                r = await client.get(lookup_url)
                if r.status_code == 200:
                    item_data = r.json()
                    real_url = item_data.get("url", "")
                    title = item_data.get("title", "")
                    item_type = item_data.get("type", "")
                    print(f"\n  Resolved {iid}:")
                    print(f"    Title: {title!r}, Type: {item_type!r}")
                    print(f"    Real URL: {real_url!r}")
                    if real_url and re.search(r"(FeatureServer|MapServer)", real_url, re.IGNORECASE):
                        await test_featureserver(client, real_url)
            except Exception as e:
                print(f"    ⚠ Lookup failed for {iid}: {e}")


async def recon_edinburgh_round2():
    print(f"\n{'=' * 70}")
    print("ROUND 2: Edinburgh — fetching Web AppBuilder config directly")
    print("=" * 70)

    async with httpx.AsyncClient(timeout=30) as client:
        url = f"https://{EDINBURGH_ORG_HOST}/sharing/rest/content/items/{EDINBURGH_WEBAPPVIEWER_ITEM_ID}/data?f=json"
        print(f"  Fetching: {url}")
        try:
            r = await client.get(url)
            print(f"  Real HTTP status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                found_urls = find_urls_in_json(data)
                print(f"  Real FeatureServer/MapServer URLs found: {len(found_urls)}")
                for u in found_urls:
                    print(f"    {u}")
                with open("/tmp/edinburgh_webappviewer_config.json", "w") as f:
                    json.dump(data, f, indent=2)
                print("  Saved: /tmp/edinburgh_webappviewer_config.json")

                for u in found_urls:
                    await test_featureserver(client, u)
            else:
                print(f"  ⚠ Non-200. First 500 chars: {r.text[:500]!r}")
        except Exception as e:
            print(f"  ⚠ Request failed: {type(e).__name__}: {e!r}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow/Edinburgh ArcGIS recon round 2\n")

    await recon_glasgow_round2()
    await recon_edinburgh_round2()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
