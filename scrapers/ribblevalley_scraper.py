#!/usr/bin/env python3
"""
PlanFind — Ribble Valley Borough Council scraper (2026-09-01).

Real, confirmed evidence backing every design decision — see
ribblevalley_councils.py.

ARCHITECTURE: genuinely the simplest tier in the whole project — every
step is a plain GET request, no session/CSRF/cookie needed anywhere.
Pure httpx throughout, no Playwright.

HONEST LIMITATION: the only date-range search is by DECISION date, not
received/submitted date — recently-submitted-but-undecided
applications from the window may be under-covered. See
ribblevalley_councils.py's module docstring.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ribblevalley_councils import COUNCIL_DB_IDS, BASE_URL, RESULTS_URL

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))

COUNCIL_NAME = "Ribble Valley Borough Council"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_rv_date(text: str) -> Optional[str]:
    """Real, confirmed format: DD/MM/YYYY."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_rv_status(decision_text: str, planning_status: str) -> str:
    """Real, confirmed: the Decision field (when present) has genuine
    specific outcome text (e.g. 'APPROVED WITH CONDITIONS', 'REFUSED')
    — much richer than most platforms in this project. Falls back to
    Planning Status (e.g. 'Decided - Final Decision') only to detect
    'decided but no clear outcome text', never to guess an outcome."""
    if decision_text:
        key = decision_text.lower()
        if any(x in key for x in ("approv", "grant", "permit")):
            return "approved"
        if any(x in key for x in ("refus", "reject")):
            return "refused"
        if "withdraw" in key:
            return "withdrawn"

        if key not in _STATUS_DIAGNOSED:
            _STATUS_DIAGNOSED.add(key)
            _log(f"⚠ STATUS DIAGNOSTIC: unrecognised decision text "
                 f"{decision_text!r} — filed as 'pending'")
        return "pending"

    return "pending"


def _parse_results_page(html: str) -> tuple[list[dict], Optional[int]]:
    """Real, confirmed structure: one <tr> per application, cell 1 =
    reference (linked to the real opaque detail URL), cell 2 =
    applicant name + address."""
    soup = BeautifulSoup(html, "html.parser")

    body_text = soup.get_text()
    total_match = re.search(r"of\s+(\d+)\s+results", body_text)
    real_total = int(total_match.group(1)) if total_match else None

    apps = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        link = cells[0].find("a")
        if not link:
            continue

        reference = link.get_text(strip=True)
        detail_url = urljoin(BASE_URL, link["href"])

        applicant_cell_text = cells[1].get_text("\n", strip=True)
        lines = [l for l in applicant_cell_text.split("\n") if l.strip()]
        applicant_name = lines[0] if lines else None
        address = lines[1] if len(lines) > 1 else None

        apps.append({
            "reference": reference,
            "applicant_name": applicant_name,
            "address": address,
            "postcode": _extract_postcode(address or ""),
            "council_url": detail_url,
        })

    return apps, real_total


def _parse_detail_page(html: str) -> dict:
    """Real, confirmed structure: proposal in <p class="first">, then a
    clean label/value <table class="planningTable">."""
    soup = BeautifulSoup(html, "html.parser")

    proposal_p = soup.find("p", class_="first")
    proposal = proposal_p.get_text(" ", strip=True) if proposal_p else None

    fields: dict[str, str] = {}
    table = soup.find("table", class_="planningTable")
    if table:
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) != 2:
                continue
            label = cells[0].get_text(strip=True)
            value = cells[1].get_text("\n", strip=True)
            fields[label] = value

    key_dates = fields.get("Key dates", "")
    received_match = re.search(r"Received\s*:\s*([\d/]+)", key_dates)
    registered_match = re.search(r"Registered\s*:\s*([\d/]+)", key_dates)

    decision_block = fields.get("Decision", "")
    decision_lines = [l for l in decision_block.split("\n") if l.strip()]
    decision_text = decision_lines[0] if decision_lines else None
    decision_date_match = re.search(r"Date\s*:\s*([\d/]+)", decision_block)

    planning_status = fields.get("Planning Status", "")

    return {
        "description": proposal,
        "submitted_date": _parse_rv_date(received_match.group(1)) if received_match else None,
        "registered_date": _parse_rv_date(registered_match.group(1)) if registered_match else None,
        "decision_date": _parse_rv_date(decision_date_match.group(1)) if decision_date_match else None,
        "status": _normalise_rv_status(decision_text, planning_status),
        "agent": fields.get("Agent"),
        "officer": fields.get("Officer"),
    }


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

    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30, follow_redirects=True) as client:
        lower_limit = 0
        real_total = None

        while True:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at lowerLimit={lower_limit}")
                break

            params = {
                "location": "", "applicant": "", "developmentDescription": "",
                "decisionType": "", "decisionDate": "",
                "fromDay": str(start.day), "fromMonth": str(start.month), "fromYear": str(start.year),
                "toDay": str(today.day), "toMonth": str(today.month), "toYear": str(today.year),
                "advancedSearch": "Search",
            }
            if lower_limit > 0:
                params["lowerLimit"] = str(lower_limit)

            try:
                r = await client.get(RESULTS_URL, params=params)
                r.raise_for_status()
            except Exception as e:
                _log(f"⚠ Results request failed at lowerLimit={lower_limit}: {type(e).__name__}: {e!r}")
                break

            page_apps, real_total = _parse_results_page(r.text)
            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1

            _log(f"lowerLimit={lower_limit}: {new_count} new (running total {len(all_apps)}"
                 + (f" of {real_total} real total" if real_total else "") + ")")

            if not page_apps:
                break
            if real_total is not None and len(all_apps) >= real_total:
                break

            lower_limit += 10
            await asyncio.sleep(0.3)

        # Fetch each detail page for the fuller fields
        for i, app in enumerate(all_apps):
            if should_stop():
                _log(f"⚠ Time budget reached during detail fetch at {i}/{len(all_apps)}")
                break
            try:
                dr = await client.get(app["council_url"])
                if dr.status_code == 200:
                    app.update(_parse_detail_page(dr.text))
            except Exception as e:
                _log(f"⚠ Detail fetch failed for {app['reference']}: {e}")
            await asyncio.sleep(0.3)

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Ribble Valley scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"ribblevalley_councils.py. Run the INSERT_SQL there, look "
              f"up the real id, and fill it in before running this "
              f"scraper.")
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

        applicant_line = f"Applicant: {a['applicant_name']}" if a.get("applicant_name") else None
        agent_line = f"Agent: {a['agent']}" if a.get("agent") else None
        description_parts = [p for p in (a.get("description"), applicant_line, agent_line) if p]

        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "submitted_date": a.get("submitted_date"),
            "decision_date": a.get("decision_date"),
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": " | ".join(description_parts) or None,
            "status": a.get("status", "pending"),
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "ribblevalley_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "ribblevalley_bespoke",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
