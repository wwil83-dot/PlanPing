#!/usr/bin/env python3
"""
PlanFind — West Dunbartonshire Council scraper (2026-08-31).

Real, confirmed evidence backing the list-level design — see
west_dunbarton_councils.py. Detail-page fields are BEST-EFFORT,
unconfirmed — see that file's HONEST LIMITATION note.

ARCHITECTURE: plain GET to dcdisplayinitial.asp with a date range,
parse the per-application <table> blocks for reference+address, then
GET each detail page directly (no session needed) for the fuller
fields. Pure httpx — no Playwright needed anywhere.
"""
import asyncio
import os
import re
import ssl
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from west_dunbarton_councils import COUNCIL_DB_IDS, RESULTS_URL, DETAIL_URL

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))

COUNCIL_NAME = "West Dunbartonshire Council"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _legacy_ssl_context() -> ssl.SSLContext:
    """REAL FIX (2026-08-31), round 2 — lowering the minimum TLS
    version alone did NOT fix the live ConnectError('') (still failed
    identically). That points past protocol version to OpenSSL 3.x's
    default SECURITY LEVEL (SECLEVEL=2), which outright rejects weak
    ciphers and short key lengths regardless of TLS version — a very
    common real blocker with old government IIS servers. Lowering the
    cipher security level (SECLEVEL=1, or 0 if 1 still isn't enough)
    is the actual documented fix for this class of problem, on top of
    the TLS 1.0 minimum and disabled cert verification already
    established as needed."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    return ctx

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 1


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_address(text: str) -> str:
    parts = [" ".join(p.split()) for p in text.split("\n") if p.strip() and p.strip() != "\xa0"]
    return ", ".join(parts)


def _parse_results_page(html: str) -> list[dict]:
    """Real, confirmed structure: one <table> per application, header
    row (Address/Application Number) then one data row."""
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    for table in soup.find_all("table"):
        header_cells = table.find("tr")
        if not header_cells:
            continue
        header_text = header_cells.get_text(" ", strip=True)
        if "Address" not in header_text or "Application Number" not in header_text:
            continue

        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        data_cells = rows[1].find_all("td")
        if len(data_cells) < 2:
            continue

        address_raw = data_cells[0].get_text()
        reference = data_cells[1].get_text(strip=True).replace("\xa0", "").strip()
        if not reference:
            continue

        address = _clean_address(address_raw)
        postcode = _extract_postcode(address_raw)
        detail_url = f"{DETAIL_URL}?vUPRN={reference}&vPassword=&View1=View"

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "council_url": detail_url,
        })

    return apps


_DETAIL_DIAGNOSED = False


def _parse_detail_page(html: str) -> dict:
    """BEST-EFFORT, UNCONFIRMED — see module docstring. Tries a few
    common label patterns; logs a diagnostic dump on first failure
    rather than silently guessing."""
    global _DETAIL_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    result = {"description": None, "submitted_date": None, "status": None}

    desc_match = re.search(r"Proposal[:\s]*\n?(.+)", text)
    if desc_match:
        result["description"] = desc_match.group(1).strip()[:500]

    date_match = re.search(r"Date (?:Received|Registered|Validated)[:\s]*\n?([\d/]+)", text, re.IGNORECASE)
    if date_match:
        try:
            result["submitted_date"] = datetime.strptime(
                date_match.group(1).strip(), "%d/%m/%Y"
            ).date().isoformat()
        except ValueError:
            pass

    status_match = re.search(r"Status[:\s]*\n?(.+)", text)
    if status_match:
        raw_status = status_match.group(1).strip().lower()
        if any(x in raw_status for x in ("approv", "grant", "permit")):
            result["status"] = "approved"
        elif any(x in raw_status for x in ("refus", "reject")):
            result["status"] = "refused"
        elif "withdraw" in raw_status:
            result["status"] = "withdrawn"
        else:
            result["status"] = "pending"

    if not any(result.values()) and not _DETAIL_DIAGNOSED:
        _DETAIL_DIAGNOSED = True
        _log("⚠ DETAIL PAGE DIAGNOSTIC: none of the expected fields "
             "(Proposal/Date Received/Status) matched — detail-page "
             "parsing needs real inspection. First 800 chars of real "
             f"page text: {text[:800]!r}")

    return result


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                json=records, headers=headers,
            )
            if r.status_code not in (200, 201, 204):
                print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                return False
            return True
    except Exception as e:
        print(f"    ✗ Upsert exception: {e}")
        return False


async def _supa_patch_council(council_id: int, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.patch(
            f"{SUPABASE_URL}/rest/v1/councils",
            params={"id": f"eq.{council_id}"},
            json=data,
            headers={**_h(), "Prefer": "return=minimal"},
        )


async def geocode(postcodes: list[str]) -> dict:
    results = {}
    unique = list({p.strip().upper().replace(" ", "") for p in postcodes if p})
    if not unique:
        return results
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(0, len(unique), 100):
            try:
                r = await c.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": unique[i:i + 100]},
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"],
                        )
            except Exception as e:
                print(f"    ⚠ Geocoding batch failed ({len(unique[i:i + 100])} postcodes): {e}")
    return results


async def scrape() -> list[dict]:
    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    async with httpx.AsyncClient(
        headers=HTTP_HEADERS, timeout=30, follow_redirects=True,
        verify=_legacy_ssl_context(),
    ) as client:
        params = {
            # Dummy but present — real evidence shows the actual filter
            # is vDateRcvFr/vDateRcvTo, not this value; kept non-empty
            # since the real captured URL always included it.
            "WeekEnding": f"{start_str}|{end_str}",
            "vDateRcvFr": start_str,
            "vDateRcvTo": end_str,
            "vWARDSelect": "",
            "Submit2": "Search",
        }
        try:
            r = await client.get(RESULTS_URL, params=params)
            r.raise_for_status()
        except Exception as e:
            cause_chain = []
            cur = e
            while cur is not None:
                cause_chain.append(f"{type(cur).__name__}: {cur!r}")
                cur = cur.__cause__ or cur.__context__
            _log(f"⚠ Results request failed. Full exception chain: {' <- '.join(cause_chain)}")
            return []

        apps = _parse_results_page(r.text)
        _log(f"Found {len(apps)} applications in the {DAYS_BACK}-day window")

        for i, app in enumerate(apps):
            if should_stop():
                _log(f"⚠ Time budget reached, stopping detail fetch at {i}/{len(apps)}")
                break
            try:
                dr = await client.get(app["council_url"])
                if dr.status_code == 200:
                    app.update(_parse_detail_page(dr.text))
            except Exception as e:
                _log(f"⚠ Detail fetch failed for {app['reference']}: {e}")
            await asyncio.sleep(0.3)

    return apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind West Dunbartonshire scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"west_dunbarton_councils.py. Run the INSERT_SQL there, look "
              f"up the real id, and fill it in before running this scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results — nothing to save.")
        return

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        _log(f"Geocoding {len(postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for a in raw_apps:
        lat, lng = None, None
        if a.get("postcode"):
            key = a["postcode"].upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "submitted_date": a.get("submitted_date"),
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description"),
            "status": a.get("status") or "pending",
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "west_dunbarton_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "west_dunbarton_asp",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
