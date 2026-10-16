#!/usr/bin/env python3
"""
Shared helpers for the council-published weekly-list scrapers
(aberdeenshire_scraper.py, east_lothian_scraper.py).

These read lists the councils publish themselves on their own websites
(PDF / Word / RTF). They never touch the Idox Public Access portals.

Plain synchronous code on purpose: tiny volumes (a handful of small files per run).
"""
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# Honest identification. Override with SCRAPER_USER_AGENT if a site ever refuses it.
USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "PlanFindBot/1.0 (+https://planfind.co.uk/about; planfind@gmail.com)",
).strip()

REQUEST_DELAY_SECONDS = float(os.environ.get("REQUEST_DELAY_SECONDS", "2"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "40"))

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b")


def log(msg: str) -> None:
    print(msg, flush=True)


def extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    found = POSTCODE_RE.findall(text.upper())
    return found[-1] if found else None


def web_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    )


def polite_get(client: httpx.Client, url: str) -> httpx.Response:
    """GET with a pause beforehand. Raises on HTTP errors."""
    time.sleep(REQUEST_DELAY_SECONDS)
    r = client.get(url)
    r.raise_for_status()
    return r


# --------------------------------------------------------------------------- Supabase
def _h(extra: Optional[dict] = None) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def require_supabase() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        log("ERROR: set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)


def supa_get(table: str, **params) -> list:
    r = httpx.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_h(), timeout=30)
    r.raise_for_status()
    return r.json()


def supa_patch_council(council_id: int, data: dict) -> None:
    httpx.patch(
        f"{SUPABASE_URL}/rest/v1/councils",
        params={"id": f"eq.{council_id}"},
        json=data,
        headers=_h({"Prefer": "return=minimal"}),
        timeout=30,
    )


def supa_upsert(records: list) -> bool:
    try:
        r = httpx.post(
            f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
            json=records,
            headers=_h({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            timeout=60,
        )
        if r.status_code not in (200, 201, 204):
            log(f"    x Upsert HTTP {r.status_code}: {r.text[:300]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log(f"    x Upsert exception: {e}")
        return False


def find_council(name: str) -> dict:
    """Look up the councils row by exact name (or COUNCIL_ID env override)."""
    forced = os.environ.get("COUNCIL_ID", "").strip()
    if forced:
        rows = supa_get("councils", select="id,name,portal_url", id=f"eq.{forced}")
    else:
        rows = supa_get("councils", select="id,name,portal_url", name=f"eq.{name}")
    if not rows:
        log(f"ERROR: council '{name}' not found in the councils table")
        sys.exit(1)
    return rows[0]


# --------------------------------------------------------------------------- geocoding
def geocode_postcodes(postcodes: list[str]) -> dict:
    """postcodes.io bulk lookup -> {POSTCODENOSPACE: (lat, lng)}."""
    out = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    for i in range(0, len(unique), 100):
        try:
            r = httpx.post("https://api.postcodes.io/postcodes",
                           json={"postcodes": unique[i:i + 100]}, timeout=20)
            for item in r.json().get("result", []):
                if item and item.get("result"):
                    out[item["query"]] = (item["result"]["latitude"], item["result"]["longitude"])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return out


_TRANSFORMER = None


def grid_to_latlng(easting: Optional[int], northing: Optional[int]) -> Optional[tuple]:
    """British National Grid -> (lat, lng). Needs pyproj; returns None if unavailable."""
    global _TRANSFORMER
    if easting is None or northing is None:
        return None
    try:
        if _TRANSFORMER is None:
            from pyproj import Transformer
            _TRANSFORMER = Transformer.from_crs(27700, 4326, always_xy=True)
        lng, lat = _TRANSFORMER.transform(easting, northing)
        if 49 < lat < 61 and -9 < lng < 2:
            return lat, lng
    except Exception:  # noqa: BLE001
        pass
    return None


def fill_coordinates(apps: list[dict]) -> None:
    """Fills lat/lng: existing value, else postcode lookup, else (last resort) the
    median of this batch's located applications, flagged geocode_quality='centroid'."""
    need = [a["postcode"] for a in apps if not a.get("lat") and a.get("postcode")]
    if need:
        coords = geocode_postcodes(need)
        for a in apps:
            if not a.get("lat") and a.get("postcode"):
                pc = a["postcode"].strip().upper().replace(" ", "")
                if pc in coords:
                    a["lat"], a["lng"] = coords[pc]
    located = [(a["lat"], a["lng"]) for a in apps if a.get("lat") and a.get("lng")]
    if located:
        c_lat = statistics.median(x[0] for x in located)
        c_lng = statistics.median(x[1] for x in located)
        n = 0
        for a in apps:
            if not a.get("lat"):
                a["lat"], a["lng"] = c_lat, c_lng
                a["geocode_quality"] = "centroid"
                n += 1
        if n:
            log(f"    centroid fallback used for {n} application(s)")


# --------------------------------------------------------------------------- saving
def save_applications(council: dict, apps: list[dict], coverage_source: str,
                      source_label: str, link_default: str) -> int:
    """Upserts applications for one council and refreshes the councils row.
    Returns the number saved. Only touches the council row on a clean save."""
    cid = council["id"]
    now = datetime.now(timezone.utc).isoformat()

    if not apps:
        supa_patch_council(cid, {"last_scraped_at": now})
        log("    nothing to save")
        return 0

    fill_coordinates(apps)

    seen, records = set(), []
    for a in apps:
        ref = a["reference"]
        if ref in seen:
            continue
        seen.add(ref)
        records.append({
            "council_id": cid,
            "reference": ref,
            "address": a.get("address"),
            "postcode": a.get("postcode"),
            "lat": a.get("lat"),
            "lng": a.get("lng"),
            "geocode_quality": a.get("geocode_quality"),
            "description": a.get("description"),
            "application_type": a.get("application_type"),
            "status": a.get("status", "pending"),
            "submitted_date": a.get("submitted_date"),
            "decision_date": a.get("decision_date"),
            "council_url": a.get("council_url") or link_default,
            "source": source_label,
        })

    # geocode_quality is only meaningful when set; drop None so existing values are not overwritten
    for r in records:
        if r.get("geocode_quality") is None:
            r.pop("geocode_quality")

    saved, ok = 0, True
    for i in range(0, len(records), 20):
        chunk = records[i:i + 20]
        # one upsert call needs identical keys per row
        keys = set().union(*[set(c) for c in chunk])
        for c in chunk:
            for k in keys:
                c.setdefault(k, None)
        if supa_upsert(chunk):
            saved += len(chunk)
        else:
            ok = False

    if ok:
        supa_patch_council(cid, {
            "coverage_source": coverage_source,
            "last_scraped_at": now,
            "last_saved_at": now,
            "consecutive_empty_runs": 0,
            "active": True,
        })
        log(f"    saved {saved}")
    else:
        log(f"    PARTIAL save: {saved} of {len(records)} (see upsert errors above)")
        if saved:
            supa_patch_council(cid, {"last_saved_at": now, "consecutive_empty_runs": 0})
    return saved
