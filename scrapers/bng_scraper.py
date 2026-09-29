#!/usr/bin/env python3
"""
PlanFind — Biodiversity Net Gain register scraper (2026-09-27).

REAL, CONFIRMED via direct exploration this session: the Defra/Natural
England biodiversity gain sites register has no bulk export, but
searching term=BGS returns the whole register (~395 sites) at 100 per
page, and each site has a predictable detail page
(/search/BGS-xxxxxxxxx) with Gain site / Habitat / Allocation /
Amendments tabs. Parsing logic below is copied from
bng_full_extract.py, the exploratory script that confirmed all of it
works against real production data — see that script's own history
in this project for the full evidence trail.

HONEST LIMITATIONS, deliberately surfaced here and in the UI, not
just in this docstring:
  - council_id is a best-effort match via reverse-geocoding the site's
    OS grid reference through postcodes.io, then matching the
    returned admin_district name against PlanFind's own councils
    table. Confirmed accurate for ~392 of ~395 sites in testing, not a
    guaranteed-perfect boundary match.
  - registering_body is the legal Section 106 authority or covenant
    holder (per Gov.uk's own registration guidance), NOT necessarily
    who to contact about buying units. Never present this as a
    landowner or seller contact.
  - Allocation planning references very often predate PlanFind's own
    coverage of that council (confirmed directly for Durham: every
    sampled real allocation reference was from 2024/2025, while
    PlanFind's earliest Durham record is July 2026) — most
    allocations will NOT match a real planning_applications row, and
    that's expected, not a bug to chase.
  - The register itself has no prices, availability, or landowner
    contact information at all. This data can only ever point someone
    toward the real register for further research — never present it
    as more complete than that.
"""
import asyncio
import csv
import io
import os
import re
import sys
import time
from collections import Counter

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,
}

BASE_URL = "https://environment.data.gov.uk/biodiversity-net-gain"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WORKERS = int(os.environ.get("WORKERS", "3"))
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "40"))
START = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 3


# ------------------------------------------------------------ parsing
# Confirmed working against real production data in
# bng_full_extract.py — copied here unchanged.

def parse_gain_site(text: str) -> dict:
    size = re.search(r"Gain site size\s*\n\s*([\d.,]+)\s*hectares", text)
    grid = re.search(r"Grid reference\s*\n\s*([A-Z]{2}\s?\d+)", text)
    body = re.search(r"Local Planning Authority or responsible body\s*\n\s*([^\n]+)", text)
    return {
        "size_ha": float(size.group(1).replace(",", "")) if size else None,
        "grid_reference": grid.group(1).replace(" ", "") if grid else None,
        "registering_body": body.group(1).strip() if body else None,
    }


def parse_habitat(text: str) -> dict:
    out = {"baseline": {}, "planned": {}}
    idx = text.find("Planned habitat improvement")
    chunks = {
        "baseline": text[:idx] if idx != -1 else text,
        "planned": text[idx:] if idx != -1 else "",
    }
    for key, chunk in chunks.items():
        for m in re.finditer(
            r"^(Area|Hedgerow|Watercourse)\tCondition\t[^\n]*\n.*?^Total\t\t([\d.,]+)",
            chunk, re.S | re.M,
        ):
            out[key][m.group(1)] = float(m.group(2).replace(",", ""))
    return out


def parse_allocations(text: str) -> list:
    allocations = []
    for part in re.split(r"^Local Planning Authority\t", text, flags=re.M)[1:]:
        lpa = part.split("\n", 1)[0].strip()
        ref = re.search(r"^Planning reference number\t([^\n]*)", part, re.M)
        proj = re.search(r"^Project name\t([^\n]*)", part, re.M)
        units = {}
        vm = re.search(r"Habitat type\tValue\n((?:[^\t\n]+\t[\d.,]+\n)+)", part)
        if vm:
            for line in vm.group(1).strip().split("\n"):
                k, v = line.split("\t")
                units[k.strip()] = float(v.replace(",", ""))
        allocations.append({
            "lpa": lpa,
            "planning_ref": ref.group(1).strip() if ref else None,
            "project_name": proj.group(1).strip() if proj else None,
            "units": units,
        })
    return allocations


# ---------------------------------------------------------- geography
# Confirmed working: converted 393/395 real grid references correctly
# in testing, all landing within real UK bounds.

_T = None


def _transformer():
    global _T
    if _T is None:
        from pyproj import Transformer
        _T = Transformer.from_crs(27700, 4326, always_xy=True)
    return _T


def grid_to_latlng(gridref: str):
    g = re.sub(r"\s", "", (gridref or "").upper())
    m = re.match(r"^([A-Z]{2})(\d+)$", g)
    if not m or len(m.group(2)) % 2:
        return None
    letters, digits = m.groups()
    l1, l2 = ord(letters[0]) - 65, ord(letters[1]) - 65
    if l1 > 7:
        l1 -= 1
    if l2 > 7:
        l2 -= 1
    e100 = ((l1 - 2) % 5) * 5 + (l2 % 5)
    n100 = (19 - (l1 // 5) * 5) - (l2 // 5)
    half = len(digits) // 2

    def centre(s: str) -> int:
        pad = 5 - len(s)
        return int(s + "5" + "0" * (pad - 1)) if pad > 0 else int(s)

    easting = e100 * 100000 + centre(digits[:half])
    northing = n100 * 100000 + centre(digits[half:])
    lng, lat = _transformer().transform(easting, northing)
    return lat, lng


async def reverse_geocode(points):
    """[(ref, lat, lng)] -> {ref: admin_district} via postcodes.io."""
    out = {}
    async with httpx.AsyncClient(timeout=30) as c:
        for i in range(0, len(points), 100):
            chunk = points[i:i + 100]
            payload = {"geolocations": [
                {"longitude": lng, "latitude": lat, "radius": 2000, "limit": 1}
                for _, lat, lng in chunk
            ]}
            try:
                r = await c.post("https://api.postcodes.io/postcodes", json=payload)
                for (ref, _, _), item in zip(chunk, r.json().get("result", [])):
                    res = item.get("result")
                    if res:
                        out[ref] = res[0].get("admin_district")
            except Exception as e:
                print(f"    ⚠ reverse geocode batch failed: {type(e).__name__}: {e}")
    return out


FILLER = {"council", "borough", "city", "district", "county", "metropolitan", "royal",
          "london", "of", "the", "unitary", "authority", "lpa", "and"}


def council_key(name: str) -> str:
    tokens = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", " and ")).split()
    core = [t for t in tokens if t not in FILLER]
    return " ".join(core) or " ".join(t for t in tokens if t not in {"lpa", "council"})


def map_council_names(names, councils):
    """{register name: PlanFind council id or None} — first match only,
    for a single best-effort id per name (unlike the exploratory
    script's multi-id version, since a stored row needs one value)."""
    by_key = {}
    for c in councils:
        by_key.setdefault(council_key(c["name"]), c["id"])
    mapping = {}
    for name in set(names):
        key = council_key(name)
        if key in by_key:
            mapping[name] = by_key[key]
            continue
        match = next((cid for k, cid in by_key.items() if k.startswith(key + " ")), None)
        mapping[name] = match
    return mapping


# ------------------------------------------------------------ scraping

async def wait_for_content(page):
    try:
        await page.wait_for_function(
            "() => !document.body.innerText.includes('Loading Message')", timeout=30_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(0.5)


async def load(page, url: str) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await wait_for_content(page)
    return await page.locator("body").inner_text()


async def click_tab(page, name: str, marker: str) -> str:
    target = page.locator(
        f"[role='tab']:has-text('{name}'), a:has-text('{name}'), button:has-text('{name}')"
    ).first
    await target.click(timeout=15_000)
    text = ""
    for _ in range(8):
        await asyncio.sleep(1)
        text = await page.locator("body").inner_text()
        if marker in text and "Loading Message" not in text:
            return text
    return text


async def enumerate_refs(page):
    refs = {}
    for page_num in range(1, 10):
        body = await load(page, f"{BASE_URL}/search?term=BGS&page={page_num}&resultsPerPage=100")
        found = list(dict.fromkeys(re.findall(r"BGS-\d+", body)))
        if not found:
            break
        for r in found:
            refs[r] = True
    return list(refs)


async def scrape_site(page, ref: str) -> dict:
    record = {"reference": ref}
    text = await load(page, f"{BASE_URL}/search/{ref}")
    record.update(parse_gain_site(text))
    boundary = page.locator("a:has-text('Link to land boundary')")
    record["land_boundary_url"] = (
        await boundary.first.evaluate("el => el.href") if await boundary.count() > 0 else None
    )
    habitat = parse_habitat(await click_tab(page, "Habitat", "Habitat information"))
    record["baseline_area_ha"] = habitat["baseline"].get("Area")
    record["baseline_hedgerow_km"] = habitat["baseline"].get("Hedgerow")
    record["planned_area_ha"] = habitat["planned"].get("Area")
    record["planned_hedgerow_km"] = habitat["planned"].get("Hedgerow")
    atext = await click_tab(page, "Allocation", "Allocation information")
    record["allocations"] = parse_allocations(atext)
    return record


async def worker(context, queue, results, errors, total):
    page = await context.new_page()
    while True:
        if should_stop():
            break
        try:
            ref = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        record, last = None, ""
        for _ in range(3):
            try:
                record = await scrape_site(page, ref)
                break
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                await asyncio.sleep(2)
        if record is None:
            errors[ref] = last
        else:
            results[ref] = record
        done = len(results) + len(errors)
        if done % 50 == 0:
            print(f"    progress: {done}/{total} sites ({elapsed_minutes():.1f} min)")
    await page.close()


# -------------------------------------------------------------- saving

def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def fetch_councils(client: httpx.AsyncClient) -> list:
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/councils",
        params={"select": "id,name", "active": "eq.true", "limit": "2000"},
        headers=_h(),
    )
    return r.json() if r.status_code == 200 else []


async def upsert_sites(client: httpx.AsyncClient, records: list) -> bool:
    if not records:
        return True
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    r = await client.post(
        f"{SUPABASE_URL}/rest/v1/bng_gain_sites?on_conflict=reference",
        json=records, headers=headers,
    )
    if r.status_code not in (200, 201, 204):
        print(f"    ✗ Sites upsert HTTP {r.status_code}: {r.text[:300]}")
        return False
    return True


async def upsert_allocations(client: httpx.AsyncClient, records: list) -> bool:
    if not records:
        return True
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    ok = True
    for i in range(0, len(records), 500):
        chunk = records[i:i + 500]
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/bng_allocations"
            f"?on_conflict=gain_site_reference,planning_reference,lpa_name",
            json=chunk, headers=headers,
        )
        if r.status_code not in (200, 201, 204):
            print(f"    ✗ Allocations upsert HTTP {r.status_code}: {r.text[:300]}")
            ok = False
    return ok


async def main():
    print(f"PlanFind BNG register scraper — workers={WORKERS}, budget={MAX_MINUTES} min\n")
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        accept = page.locator("button:has-text('Accept all cookies')")
        if await accept.count() > 0:
            await accept.first.click(timeout=5_000)

        print("Enumerating the register…")
        refs = await enumerate_refs(page)
        print(f"  found {len(refs)} real references")
        await page.close()

        queue = asyncio.Queue()
        for r in refs:
            queue.put_nowait(r)
        results, errors = {}, {}
        print(f"\nScraping {len(refs)} sites with {WORKERS} workers…")
        await asyncio.gather(*[
            worker(context, queue, results, errors, len(refs)) for _ in range(WORKERS)
        ])
        await context.close()
        await browser.close()

    sites = [results[r] for r in refs if r in results]
    print(f"\nScraped {len(sites)} of {len(refs)} sites ({len(errors)} failed)")
    for ref, err in list(errors.items())[:5]:
        print(f"  failed {ref}: {err}")

    print("\nConverting grid references and reverse-geocoding…")
    points = []
    for s in sites:
        ll = grid_to_latlng(s["grid_reference"]) if s.get("grid_reference") else None
        s["lat"], s["lng"] = ll if ll else (None, None)
        if ll:
            points.append((s["reference"], ll[0], ll[1]))
    council_of_site = await reverse_geocode(points)
    print(f"  {len(points)} of {len(sites)} converted, "
          f"{len(council_of_site)} resolved to a district")

    async with httpx.AsyncClient(timeout=90) as client:
        councils = await fetch_councils(client)
        print(f"  {len(councils)} real active councils loaded for matching")

        site_district_names = list(council_of_site.values())
        allocation_lpa_names = [
            a["lpa"] for s in sites for a in s["allocations"]
        ]
        name_to_id = map_council_names(site_district_names + allocation_lpa_names, councils)

        site_records = []
        for s in sites:
            district = council_of_site.get(s["reference"])
            council_id = name_to_id.get(district) if district else None
            site_records.append({
                "reference": s["reference"],
                "size_ha": s.get("size_ha"),
                "grid_reference": s.get("grid_reference"),
                "lat": s.get("lat"),
                "lng": s.get("lng"),
                "council_id": council_id,
                "council_match_confidence": "reverse_geocoded" if council_id else None,
                "registering_body": s.get("registering_body"),
                "land_boundary_url": s.get("land_boundary_url"),
                "baseline_area_ha": s.get("baseline_area_ha"),
                "baseline_hedgerow_km": s.get("baseline_hedgerow_km"),
                "planned_area_ha": s.get("planned_area_ha"),
                "planned_hedgerow_km": s.get("planned_hedgerow_km"),
                "last_scraped_at": "now()",
            })

        print(f"\nSaving {len(site_records)} gain sites…")
        ok = await upsert_sites(client, site_records)
        print("  ✓ saved" if ok else "  ✗ failed — see errors above")

        allocation_records = []
        seen_keys = set()
        duplicates_dropped = 0
        for s in sites:
            for a in s["allocations"]:
                key = (s["reference"], a.get("planning_ref"), a["lpa"])
                if key in seen_keys:
                    duplicates_dropped += 1
                    continue
                seen_keys.add(key)
                allocation_records.append({
                    "gain_site_reference": s["reference"],
                    "lpa_name": a["lpa"],
                    "council_id": name_to_id.get(a["lpa"]),
                    "planning_reference": a.get("planning_ref"),
                    "project_name": a.get("project_name"),
                    "area_units": a["units"].get("Area"),
                    "hedgerow_units": a["units"].get("Hedgerow"),
                    "watercourse_units": a["units"].get("Watercourse"),
                })
        if duplicates_dropped:
            # REAL, CONFIRMED FIX — a production run failed with
            # Postgres error 21000 ("ON CONFLICT DO UPDATE command
            # cannot affect row a second time"): the same
            # (gain_site_reference, planning_reference, lpa_name)
            # combination appeared more than once within a batch,
            # which an upsert can't resolve on its own. De-duplicating
            # here rather than assuming the real page data is always
            # unique.
            print(f"    ⚠ dropped {duplicates_dropped} duplicate allocation rows "
                  f"(same gain site + planning reference + LPA seen more than once)")
        print(f"Saving {len(allocation_records)} allocations…")
        ok2 = await upsert_allocations(client, allocation_records)
        print("  ✓ saved" if ok2 else "  ✗ failed — see errors above")

    mapped = sum(1 for r in site_records if r["council_id"])
    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Sites saved: {len(site_records)} ({mapped} matched to a real council)")
    # REAL FIX — this used to print len(allocation_records) regardless
    # of whether ok2 was True, meaning a production run reported
    # "Allocations saved: 3058" even though the upsert had actually
    # failed with real HTTP 500 errors. Reflecting the real outcome
    # instead.
    if ok2:
        print(f"Allocations saved: {len(allocation_records)}")
    else:
        print(f"Allocations: SAVE FAILED — {len(allocation_records)} were attempted, "
              f"see the ✗ errors above for the real cause")


if __name__ == "__main__":
    asyncio.run(main())
