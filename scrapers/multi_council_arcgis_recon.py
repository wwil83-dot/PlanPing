#!/usr/bin/env python3
"""
PlanFind — Tonbridge & Malling / City of London / Fife ArcGIS recon
(2026-09-04).

Real, confirmed service URLs found via public ArcGIS Services
Directory listings (not embedded inside an app wrapper like Glasgow's
was, which turned out to be token-gated) — a good sign these are
genuinely open, unlike Glasgow's specific situation.

  - Tonbridge & Malling: Agile_Maps/Planning_Applications_dashboard
    (already used as a positive control in earlier Glasgow recon)
  - City of London: COMPASS_Planning_Planning_Applications — confirmed
    via their own Geocortex sites directory + a data.gov.uk entry
    stating this dataset updates DAILY
  - Fife: TWO real candidate services found —
    Planning_Pro (real, rich status-split layers: Pending
    Consideration/Pending Decision/Appeal/Permitted/Refused/Other/
    Returned-Invalid-or-Withdrawn/All Apps) and
    Planning_Applications_LinkGISLIVE (explicitly described as
    "Planning Applications from UNIform...updates scheduled overnight
    via FME" — a real nightly sync from their live Uniform/Idox
    backend)

Pure httpx — none of these need Playwright, unlike Glasgow's app-
wrapped, auth-token-gated situation.
"""
import asyncio
from datetime import datetime, timezone

import httpx

TARGETS = [
    ("Tonbridge and Malling Borough Council",
     "https://mapsat.tmbc.gov.uk/server/rest/services/Agile_Maps/Planning_Applications_dashboard/MapServer",
     None),  # layer index unknown — will list and try each
    ("City of London Corporation",
     "https://www.mapping.cityoflondon.gov.uk/arcgis/rest/services/COMPASS_Planning_Planning_Applications/MapServer",
     None),
    ("Fife Council (Planning_Pro, 'All Apps' layer)",
     "https://arcgis-live-as.fife.gov.uk/server/rest/services/Planning_Pro/MapServer",
     7),  # real, confirmed layer index for "All Apps"
    ("Fife Council (Planning_Applications_LinkGISLIVE)",
     "https://arcgis-live-as.fife.gov.uk/server/rest/services/Planning_Applications_LinkGISLIVE/MapServer",
     None),
]


async def list_layers(client: httpx.AsyncClient, base_url: str):
    try:
        r = await client.get(f"{base_url}?f=json")
        print(f"  Real service root HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"  ⚠ Real API error: {data['error']}")
                return []
            layers = data.get("layers", [])
            print(f"  Real layers found: {len(layers)}")
            for l in layers:
                print(f"    id={l.get('id')} name={l.get('name')!r}")
            return [l.get("id") for l in layers]
    except Exception as e:
        print(f"  ⚠ Service root request failed: {type(e).__name__}: {e!r}")
    return []


async def test_layer(client: httpx.AsyncClient, base_url: str, layer_index: int):
    print(f"\n  --- Layer {layer_index} ---")
    meta_url = f"{base_url}/{layer_index}?f=json"
    try:
        r = await client.get(meta_url)
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"  ⚠ Real metadata error: {data['error']}")
                return
            print(f"  Real layer name: {data.get('name', '')!r}")
            fields = data.get("fields", [])
            print(f"  Real field names: {[f.get('name') for f in fields][:15]}")
    except Exception as e:
        print(f"  ⚠ Metadata request failed: {type(e).__name__}: {e!r}")
        return

    query_url = f"{base_url}/{layer_index}/query"
    params = {
        "where": "1=1", "outFields": "*", "f": "json",
        "resultRecordCount": "3",
    }
    try:
        r = await client.get(query_url, params=params)
        print(f"  Real query HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"  ⚠ Real query error: {data['error']}")
            else:
                features = data.get("features", [])
                print(f"  Real sample records returned: {len(features)}")
                for feat in features[:2]:
                    print(f"    {feat.get('attributes', {})}")
    except Exception as e:
        print(f"  ⚠ Query failed: {type(e).__name__}: {e!r}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Multi-council ArcGIS recon\n")

    async with httpx.AsyncClient(timeout=30) as client:
        for name, base_url, known_layer in TARGETS:
            print(f"\n{'=' * 70}")
            print(f"COUNCIL: {name}")
            print(f"Service: {base_url}")
            print("=" * 70)

            if known_layer is not None:
                await test_layer(client, base_url, known_layer)
            else:
                layer_ids = await list_layers(client, base_url)
                for lid in layer_ids[:5]:
                    await test_layer(client, base_url, lid)

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
