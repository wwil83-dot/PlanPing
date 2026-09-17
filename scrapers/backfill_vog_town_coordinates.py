#!/usr/bin/env python3
"""
PlanFind — Vale of Glamorgan town-coordinate backfill (2026-09-16).

Real, confirmed context: Vale of Glamorgan's results-list page never
includes a postcode in the address column (confirmed via 5 real
examples across two screenshots — e.g. "Cafe at Porthkerry Country
Park, Barry"), so all 89 of its currently-saved applications have no
lat/lng at all and show as "Approx." with nothing to plot on any map.

REAL FIX: UK addressing convention puts the town/area name as the
LAST comma-separated segment of an address ("...Penarth", "...Barry")
— even without a postcode, this is real, usable location information.
This script extracts that segment from each application's own stored
address, matches it against the ALREADY-POPULATED `towns` table (real
UK towns with real coordinates, used by /towns), and writes back a
genuine town-level coordinate plus geocode_quality='centroid' so it's
honestly marked as approximate everywhere is_centroid is checked, not
silently shown as a precise pin.

HONEST LIMITATION: this only matches when the address's final segment
is a real, exact (case-insensitive) town name already in the towns
table — genuinely unmatched addresses are left untouched and reported
clearly at the end, not guessed at with a lower-confidence fallback.
One-off backfill for existing data — does NOT fix future scrapes;
planning_register_scraper.py itself doesn't do this matching, so newly
saved Vale of Glamorgan applications will still need this run again
periodically until that's addressed separately.
"""
import os
import sys
import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

VALE_OF_GLAMORGAN_COUNCIL_ID = 555


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def _extract_town_candidate(address: str) -> str | None:
    """Real UK addressing convention: the town/area name is typically
    the LAST comma-separated segment. Confirmed against 5 real
    addresses during diagnosis (all ending in "...Penarth" or
    "...Barry"). Returns None for an address with no comma at all —
    too ambiguous to guess at."""
    if not address or "," not in address:
        return None
    return address.rsplit(",", 1)[-1].strip()


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    with httpx.Client(timeout=30) as client:
        # Real, confirmed target: only Vale of Glamorgan applications
        # genuinely missing a coordinate — never touches anything that
        # already has real geocoded data.
        r = client.get(
            f"{SUPABASE_URL}/rest/v1/planning_applications",
            params={
                "council_id": f"eq.{VALE_OF_GLAMORGAN_COUNCIL_ID}",
                "lat": "is.null",
                "select": "id,address",
            },
            headers=_h(),
        )
        r.raise_for_status()
        applications = r.json()
        print(f"Found {len(applications)} real applications with no coordinate")

        matched = 0
        unmatched_towns: dict[str, int] = {}

        for app in applications:
            candidate = _extract_town_candidate(app.get("address", ""))
            if not candidate:
                unmatched_towns.setdefault("(no comma in address)", 0)
                unmatched_towns["(no comma in address)"] += 1
                continue

            town_resp = client.get(
                f"{SUPABASE_URL}/rest/v1/towns",
                params={
                    "name": f"ilike.{candidate}",
                    "select": "name,lat,lng",
                    "limit": "1",
                },
                headers=_h(),
            )
            town_resp.raise_for_status()
            towns = town_resp.json()

            if not towns:
                unmatched_towns.setdefault(candidate, 0)
                unmatched_towns[candidate] += 1
                continue

            town = towns[0]
            patch_resp = client.patch(
                f"{SUPABASE_URL}/rest/v1/planning_applications",
                params={"id": f"eq.{app['id']}"},
                json={
                    "lat": town["lat"],
                    "lng": town["lng"],
                    "geocode_quality": "centroid",
                },
                headers={**_h(), "Prefer": "return=minimal"},
            )
            if patch_resp.status_code not in (200, 204):
                print(f"  ✗ Failed to update application {app['id']}: "
                      f"HTTP {patch_resp.status_code}: {patch_resp.text[:200]}")
                continue

            matched += 1
            print(f"  ✓ Application {app['id']}: {candidate!r} → "
                  f"{town['name']} ({town['lat']}, {town['lng']})")

        print(f"\n{'=' * 50}")
        print(f"Matched and updated: {matched}")
        print(f"Unmatched: {len(applications) - matched}")
        if unmatched_towns:
            print(f"\nReal unmatched candidates (town not found in towns "
                  f"table, or address had no comma):")
            for name, count in sorted(unmatched_towns.items(), key=lambda x: -x[1]):
                print(f"  {name!r}: {count}")


if __name__ == "__main__":
    main()
