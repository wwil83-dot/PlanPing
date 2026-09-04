#!/usr/bin/env python3
"""
PlanFind — Durham County Council / Lancashire ArcGIS recon (2026-09-04).

Real find: spatial.durham.gov.uk is a confirmed, existing public
ArcGIS Server for Durham County Council (one of its real services,
External/LocalPlan, covering housing allocation/policy data — NOT
planning applications specifically, but confirms the right host).

This browses Durham's full service directory looking for anything
planning-application-related, rather than guessing a specific service
name blind.

Also tests a bonus lead found in the same search: Lancashire has a
service literally named Planning_Applications_County_Council
(gis.lancashire.gov.uk) — a different county, but a real, concrete,
already-named service worth checking directly.
"""
import asyncio
import re
from datetime import datetime, timezone

import httpx

DURHAM_SERVICES_ROOT = "https://spatial.durham.gov.uk/arcgis/rest/services"
LANCASHIRE_SERVICE = "https://gis.lancashire.gov.uk/arcgis/rest/services/Hosted/Planning_Applications_County_Council/FeatureServer"


async def browse_folder(client: httpx.AsyncClient, url: str, depth: int = 0, max_depth: int = 2):
    if depth > max_depth:
        return
    try:
        r = await client.get(f"{url}?f=json")
        if r.status_code != 200:
            print(f"{'  ' * depth}⚠ HTTP {r.status_code} at {url}")
            return
        data = r.json()
    except Exception as e:
        print(f"{'  ' * depth}⚠ Failed at {url}: {type(e).__name__}: {e!r}")
        return

    folders = data.get("folders", [])
    services = data.get("services", [])

    for f in folders:
        print(f"{'  ' * depth}📁 {f}")
        marker = "🎯 " if re.search(r"planning|application", f, re.IGNORECASE) else ""
        if marker:
            print(f"{'  ' * depth}  {marker}LIKELY MATCH — worth checking directly")
        await browse_folder(client, f"{url}/{f}", depth + 1, max_depth)

    for s in services:
        name = s.get("name", "")
        stype = s.get("type", "")
        marker = "🎯 " if re.search(r"planning|application", name, re.IGNORECASE) else ""
        print(f"{'  ' * depth}{marker}📄 {name} ({stype})")


async def test_lancashire(client: httpx.AsyncClient):
    print(f"\n{'=' * 70}")
    print("TESTING: Lancashire — Planning_Applications_County_Council")
    print("=" * 70)

    # REAL RETRY (2026-09-04) — first attempt hit a ConnectTimeout, a
    # more ambiguous signature than a clean success or an explicit
    # block (could be a transient network blip, an overloaded server,
    # or a real silent block). One attempt with a standard timeout
    # isn't enough to conclude anything — retrying with a longer
    # timeout and a couple of attempts before drawing any conclusion.
    last_error = None
    for attempt in range(1, 4):
        try:
            print(f"\nAttempt {attempt}/3 (60s timeout)...")
            r = await client.get(f"{LANCASHIRE_SERVICE}?f=json", timeout=60)
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
                break
        except Exception as e:
            last_error = e
            print(f"⚠ Attempt {attempt} failed: {type(e).__name__}: {e!r}")
            if attempt < 3:
                await asyncio.sleep(5)
    else:
        print(f"\n⚠ All 3 attempts failed with a timeout/connection error — "
              f"this looks like a genuine, consistent block or an unreachable "
              f"host, not a one-off blip. Last error: {last_error!r}")
        return

    query_url = f"{LANCASHIRE_SERVICE}/0/query"
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
                for feat in features:
                    print(f"  {feat.get('attributes', {})}")
    except Exception as e:
        print(f"⚠ Query failed: {type(e).__name__}: {e!r}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Lancashire ArcGIS retest\n")
    print("(Durham already conclusively resolved in the previous run — full "
          "service directory browsed, no planning-application service found "
          "anywhere. Not re-checking here.)\n")

    async with httpx.AsyncClient(timeout=30) as client:
        await test_lancashire(client)

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
