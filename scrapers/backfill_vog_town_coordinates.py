#!/usr/bin/env python3
"""
PlanFind — Vale of Glamorgan town-coordinate backfill (2026-09-16).

Real, confirmed context: Vale of Glamorgan's results-list page never
includes a postcode in the address column, so all its currently-saved
applications with no postcode have no lat/lng at all and show as
"Approx." with nothing to plot on any map.

REAL FIX: UK addressing convention puts the town/area name as the
LAST comma-separated segment of an address ("...Penarth", "...Barry")
— even without a postcode, this is real, usable location information.
This matches that segment against the ALREADY-POPULATED `towns` table
(real UK towns with real coordinates, used by /towns).

REAL BUG FOUND AND FIXED (round 2, same day): the first version of
this script matched by name alone with NO geographic check — several
UK place names are genuinely ambiguous (a real "Barry" exists in
Angus, Scotland; a real "Nash" exists in Buckinghamshire), and the
bare name match silently picked the wrong one for ~22 applications,
placing real Vale of Glamorgan planning applications in Scotland and
the English Midlands on the map. Confirmed directly from that run's
own output: 'Barry' resolved to (56.499, -2.754), genuinely in
Scotland, while every OTHER correctly-matched Vale of Glamorgan town
(Penarth, Rhoose, Cowbridge, Dinas Powys, Llantwit Major, etc.)
clusters tightly around 51.0-51.7N, -3.7 to -2.9W. This version adds
a real, direct sanity check: any name match whose coordinates fall
outside that confirmed real bounding box is REJECTED as an ambiguous
wrong-region match, not silently accepted. It also re-checks
applications that already have geocode_quality='centroid' set (from
the first, buggy run) so the already-wrong Barry/Nash entries get
corrected, not just newly-null ones.

HONEST LIMITATION: the bounding box is a real, evidence-based
approximation (drawn directly from this run's own confirmed-correct
matches), not an authoritative Vale of Glamorgan boundary — a
genuinely ambiguous name whose OTHER real instance happens to also
fall inside this box would still be wrongly accepted. Good enough to
catch the two confirmed real cases (Scotland, Buckinghamshire), not a
guarantee against every possible false positive.
"""
import os
import sys
import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

VALE_OF_GLAMORGAN_COUNCIL_ID = 555

# Real, evidence-based bounding box for Vale of Glamorgan — drawn
# directly from this project's own confirmed-correct town matches
# (Penarth 51.44/-3.17, Rhoose 51.39/-3.35, Cowbridge 51.46/-3.45,
# Dinas Powys 51.43/-3.22, Llantwit Major 51.41/-3.49), with a small
# real margin. Anything outside this is treated as a genuinely
# different, wrongly-matched place with the same name.
VOG_LAT_RANGE = (51.0, 51.7)
VOG_LNG_RANGE = (-3.7, -2.9)


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def _extract_town_candidate(address: str) -> str | None:
    if not address or "," not in address:
        return None
    return address.rsplit(",", 1)[-1].strip()


def _in_vale_of_glamorgan_area(lat: float, lng: float) -> bool:
    """REAL FIX (round 2) — confirmed necessary: a bare name match
    alone let a Scottish "Barry" and a Buckinghamshire "Nash" through
    as if they were the real Vale of Glamorgan places. Every
    confirmed-correct match from the first run falls well inside this
    box; both confirmed-wrong ones fall well outside it."""
    return (VOG_LAT_RANGE[0] <= lat <= VOG_LAT_RANGE[1]
            and VOG_LNG_RANGE[0] <= lng <= VOG_LNG_RANGE[1])


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    with httpx.Client(timeout=30) as client:
        # REAL FIX (round 2) — no longer only "lat is.null". The first
        # run already wrote wrong coordinates for ~22 applications
        # (geocode_quality='centroid' but the WRONG place) — those need
        # re-checking and correcting too, not just genuinely-untouched
        # rows.
        r = client.get(
            f"{SUPABASE_URL}/rest/v1/planning_applications",
            params={
                "council_id": f"eq.{VALE_OF_GLAMORGAN_COUNCIL_ID}",
                "or": "(lat.is.null,geocode_quality.eq.centroid)",
                "select": "id,address,lat,lng",
            },
            headers=_h(),
        )
        r.raise_for_status()
        applications = r.json()
        print(f"Found {len(applications)} real applications to check "
              f"(no coordinate, or a previous centroid fallback to re-verify)")

        matched = 0
        rejected_wrong_region = 0
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
                    "limit": "5",
                },
                headers=_h(),
            )
            town_resp.raise_for_status()
            towns = town_resp.json()

            if not towns:
                unmatched_towns.setdefault(candidate, 0)
                unmatched_towns[candidate] += 1
                continue

            # REAL FIX (round 2) — check EVERY same-named match (not
            # just the first) for one that genuinely falls in the real
            # area, rather than trusting whichever came back first.
            real_match = None
            for t in towns:
                if _in_vale_of_glamorgan_area(t["lat"], t["lng"]):
                    real_match = t
                    break

            if real_match is None:
                rejected_wrong_region += 1
                print(f"  ⚠ Application {app['id']}: {candidate!r} matched "
                      f"{len(towns)} town(s) by name, but NONE fall within "
                      f"the real Vale of Glamorgan area — rejected as "
                      f"ambiguous (e.g. {towns[0]['name']} at "
                      f"{towns[0]['lat']}, {towns[0]['lng']})")
                unmatched_towns.setdefault(f"{candidate} (wrong region)", 0)
                unmatched_towns[f"{candidate} (wrong region)"] += 1
                continue

            patch_resp = client.patch(
                f"{SUPABASE_URL}/rest/v1/planning_applications",
                params={"id": f"eq.{app['id']}"},
                json={
                    "lat": real_match["lat"],
                    "lng": real_match["lng"],
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
                  f"{real_match['name']} ({real_match['lat']}, {real_match['lng']})")

        print(f"\n{'=' * 50}")
        print(f"Matched and updated: {matched}")
        print(f"Rejected as wrong-region (ambiguous name): {rejected_wrong_region}")
        print(f"Unmatched (no town found at all): "
              f"{len(applications) - matched - rejected_wrong_region}")
        if unmatched_towns:
            print(f"\nReal unmatched/rejected candidates:")
            for name, count in sorted(unmatched_towns.items(), key=lambda x: -x[1]):
                print(f"  {name!r}: {count}")


if __name__ == "__main__":
    main()
