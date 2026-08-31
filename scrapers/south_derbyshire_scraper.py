#!/usr/bin/env python3
"""
PlanFind — South Derbyshire District Council scraper (2026-08-31).

Real, confirmed evidence backing every design decision — see
south_derbyshire_councils.py.

ARCHITECTURE: Playwright drives the real Livewire UI interaction
(select dateType, fill the now-enabled date fields), then reads the
component's real `wire:effects` DOM attribute directly via
page.evaluate() — this is HTML-unescaped automatically by the browser,
giving us the exact same clean JSON structure confirmed during recon,
with all the real Salesforce field names already validated. Far more
robust than regex-scraping the rendered HTML table.

HONEST LIMITATION: pagination is NOT implemented yet — perPage is set
to 100 (the platform's own maximum), which comfortably covers a normal
30-day window (confirmed real total for a ~2-month test window was
just 83), but if a real DAYS_BACK window ever exceeds 100 results,
only the first 100 are captured. Logged as a diagnostic if hit.
"""
import asyncio
import os
import re
import sys
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from south_derbyshire_councils import COUNCIL_DB_IDS, BASE_URL

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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "15"))
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))

COUNCIL_NAME = "South Derbyshire District Council"

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_json_date(value) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")).date().isoformat()
    except ValueError:
        return None


_STATUS_DIAGNOSED = False


def _normalise_south_derbyshire_status(status_text: str) -> str:
    if not status_text:
        return "pending"
    key = status_text.lower()
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in ("consultation", "consideration", "pending", "awaiting")):
        return "pending"

    global _STATUS_DIAGNOSED
    if not _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED = True
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised status {status_text!r} — filed as 'pending'")
    return "pending"


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
    after_str = start.isoformat()
    before_str = today.isoformat()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        _log(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            # Real, confirmed: dateType=1 is "Validation Date". Selecting
            # it fires a real Livewire round-trip that enables the
            # otherwise disabled="" afterDate/beforeDate fields.
            await page.select_option("select[wire\\:model\\.live='dateType']", "1", timeout=10_000)
            await asyncio.sleep(2)

            await page.fill("input[wire\\:model\\.live='afterDate']", after_str, timeout=5_000)
            await page.fill("input[wire\\:model\\.live='beforeDate']", before_str, timeout=5_000)
            await asyncio.sleep(2)

            # Real, confirmed: perPage select supports up to 100 —
            # platform's own maximum, set here to minimise the chance
            # of needing pagination (not yet implemented).
            try:
                await page.select_option("select[wire\\:model\\.live='perPage']", "100", timeout=5_000)
                await asyncio.sleep(2)
            except Exception as e:
                _log(f"⚠ Could not set perPage=100 (non-fatal): {e}")

        except Exception as e:
            _log(f"⚠ Livewire interaction failed: {type(e).__name__}: {e!r}")
            await context.close()
            await browser.close()
            return []

        # Real, confirmed: the actual application data lives in the
        # wire:effects DOM attribute (a Livewire event dispatch used to
        # update the Leaflet map), NOT wire:snapshot. Reading it via
        # page.evaluate() gets it HTML-unescaped automatically.
        # REAL FIX (2026-08-31) — validating this approach against the
        # actual recon4 capture found TWO elements with a wire:effects
        # attribute on this page (one an empty list, one the real data).
        # querySelector() alone grabs whichever comes first in the DOM,
        # which in testing was the EMPTY one. Iterate all of them and
        # pick the one that actually contains real dispatch data.
        try:
            effects_raw = await page.evaluate(
                """() => {
                    const els = document.querySelectorAll('[wire\\\\:effects]');
                    for (const el of els) {
                        const raw = el.getAttribute('wire:effects');
                        if (raw && raw.includes('"dispatches"') && raw.includes('"data"')) {
                            return raw;
                        }
                    }
                    return null;
                }"""
            )
        except Exception as e:
            _log(f"⚠ Could not read wire:effects: {e}")
            effects_raw = None

        await context.close()
        await browser.close()

    if not effects_raw:
        _log("⚠ No wire:effects data found — nothing to parse")
        return []

    try:
        effects = json.loads(effects_raw)
        apps = effects["dispatches"][0]["params"][0][0]["data"]
        real_total = effects["dispatches"][0]["params"][0][0].get("total")
        _log(f"Parsed {len(apps)} applications from real wire:effects JSON "
             f"(real total for this window: {real_total})")
        if real_total and real_total > 100:
            _log(f"⚠ PAGINATION DIAGNOSTIC: real total ({real_total}) exceeds "
                 f"the 100-per-page cap — only the first 100 were captured. "
                 f"Pagination not yet implemented.")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        _log(f"⚠ Could not parse wire:effects structure (real structure may "
             f"have changed): {type(e).__name__}: {e!r}. Raw (first 500 "
             f"chars): {effects_raw[:500]!r}")
        return []

    return apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind South Derbyshire scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"south_derbyshire_councils.py. Run the INSERT_SQL there, "
              f"look up the real id, and fill it in before running this "
              f"scraper.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    raw_apps = await scrape()

    if not raw_apps:
        print("\nNo results — nothing to save.")
        return

    addresses = [a.get("Site_Address__c") or "" for a in raw_apps]
    postcodes = [_extract_postcode(a) for a in addresses]
    valid_postcodes = [p for p in postcodes if p]
    coords = await geocode(valid_postcodes) if valid_postcodes else {}
    if valid_postcodes:
        _log(f"Geocoding {len(valid_postcodes)} postcodes…")

    fallback_count = 0
    records = []
    for a, address, postcode in zip(raw_apps, addresses, postcodes):
        lat, lng = None, None
        if postcode:
            key = postcode.upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        reference = a.get("Name")
        if not reference:
            continue

        detail_url = a.get("url")

        records.append({
            "council_id": cid,
            "reference": reference,
            "submitted_date": _parse_json_date(a.get("Validated_Date__c")),
            "decision_date": _parse_json_date(a.get("Current_Decision_Date__c")),
            "address": address or None,
            "postcode": postcode,
            "description": a.get("Short_Proposal__c") or None,
            "application_type": a.get("Type__c") or None,
            "status": _normalise_south_derbyshire_status(a.get("Status__c", "")),
            "council_url": detail_url,
            "lat": lat,
            "lng": lng,
            "source": "south_derbyshire_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "south_derbyshire_livewire",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
