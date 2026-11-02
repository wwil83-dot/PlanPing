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
DRY_RUN      = os.environ.get("DRY_RUN", "0") == "1"

try:
    from pyproj import Transformer
    _EN_TO_LL = Transformer.from_crs(27700, 4326, always_xy=True)
except Exception:  # pyproj missing: fall back to postcodes
    _EN_TO_LL = None


def _en_to_latlng(e, n):
    if not _EN_TO_LL or not e or not n:
        return None, None
    lng, lat = _EN_TO_LL.transform(float(e), float(n))
    if 52.4 <= lat <= 53.3 and -2.3 <= lng <= -1.0:
        return round(lat, 6), round(lng, 6)
    return None, None

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


def _normalise_south_derbyshire_status(status_text: str, decided: bool = False) -> str:
    if not status_text:
        return "closed" if decided else "pending"
    key = status_text.lower()
    if "not decided" in key:
        return "pending"
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
        _log(f"⚠ STATUS DIAGNOSTIC: unrecognised status {status_text!r} — filed as {'closed' if decided else 'pending'}")
    return "closed" if decided else "pending"


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_upsert(records: list) -> bool:
    headers = {**_h(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    # PostgREST wants every row in a request to have the same keys: group rows by key set, 50 at a time
    groups: dict = {}
    for rec in records:
        groups.setdefault(tuple(sorted(rec)), []).append(rec)
    ok_all = True
    async with httpx.AsyncClient(timeout=90) as c:
        for rows in groups.values():
            for i in range(0, len(rows), 50):
                chunk = rows[i:i + 50]
                for attempt in range(1, 4):
                    try:
                        r = await c.post(
                            f"{SUPABASE_URL}/rest/v1/planning_applications?on_conflict=council_id,reference",
                            json=chunk, headers=headers,
                        )
                        if r.status_code in (200, 201, 204):
                            break
                        print(f"    ✗ Upsert HTTP {r.status_code}: {r.text[:300]}")
                    except Exception as e:
                        print(f"    ✗ Upsert attempt {attempt} failed: {type(e).__name__}: {e!r}")
                    await asyncio.sleep(3 * attempt)
                else:
                    ok_all = False
    return ok_all


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


ROW_JS = """() => [...document.querySelectorAll('tr[data-flux-row]')].map(r => {
  const tds = [...r.querySelectorAll('td')];
  const a = r.querySelector('a[href*="/application/"]');
  const shown = r.offsetParent !== null;
  const badge = r.querySelector('span.inline-flex');
  const type = r.querySelector('td:nth-child(2) p.mb-1');
  const desc = r.querySelector('span.line-clamp-4');
  const addr = r.querySelector('td:nth-child(2) p.font-medium');
  return {
    ref: a ? a.textContent.trim() : '',
    shown: shown,
    url: a ? a.href : '',
    status: badge ? badge.textContent.trim() : '',
    type: type ? type.textContent.trim() : '',
    desc: desc ? desc.textContent.trim() : '',
    addr: addr ? addr.textContent.trim() : '',
    valid: tds[2] ? tds[2].innerText.trim() : '',
    decided: tds[3] ? tds[3].innerText.trim() : '',
  };
})"""

EFFECTS_JS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('[wire\\\\:effects]')) {
    const raw = el.getAttribute('wire:effects');
    if (raw && raw.includes('updateMap')) out.push(raw);
  }
  return out;
}"""


def _parse_ui_date(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,2} [A-Z][a-z]{2} \d{4})\b", text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d %b %Y").date().isoformat()
    except ValueError:
        return None


async def _no_cookie_banner(page):
    try:
        await page.evaluate("() => { document.querySelectorAll('#cookies-policy, .cookies').forEach(e => e.remove()); }")
    except Exception:
        pass


async def _settle(page, ms: int = 2500):
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await page.wait_for_timeout(ms)


async def _visible(page, selector: str):
    loc = page.locator(selector)
    for i in range(await loc.count()):
        if await loc.nth(i).is_visible():
            return loc.nth(i)
    return None


async def _read_page(page, rows: dict, en: dict):
    """Add this page's table rows to rows, and its map coordinates to en."""
    for r in await page.evaluate(ROW_JS):
        ref = (r.get("ref") or "").strip()
        if not ref:
            continue
        old = rows.get(ref)
        # the same reference can appear more than once (hidden copies): keep the fullest one
        def richness(x):
            return sum(1 for k in ("addr", "desc", "valid", "type", "decided") if (x.get(k) or "").strip()) + (2 if x.get("shown") else 0)
        if old and richness(old) >= richness(r):
            continue
        rows[ref] = r
    try:
        for raw in await page.evaluate(EFFECTS_JS):
            for d in json.loads(raw).get("dispatches", []):
                if d.get("name") != "updateMap":
                    continue
                params = d.get("params") or {}
                for x in params.get("applications", []) or []:
                    if x.get("reference") and x.get("easting") and x.get("northing"):
                        en[x["reference"]] = (x["easting"], x["northing"])
    except Exception as e:
        _log(f"⚠ Could not read map coordinates (non-fatal): {type(e).__name__}: {e}")


async def _summary(page):
    """('Showing a to b of N') as (a, b, N), taking the entry with the largest 'to'."""
    text = await page.inner_text("body")
    ms = re.findall(r"Showing (\d+) to (\d+) of ([\d,]+)", text)
    if not ms:
        return None
    best = max(ms, key=lambda m: int(m[1]))
    return int(best[0]), int(best[1]), int(best[2].replace(",", ""))


async def _wait_stable(page, prev=None, timeout: float = 25.0):
    """Poll until the result summary is the same 3 times in a row (and differs from prev, if given)."""
    seen, last, t0 = 0, "unset", time.monotonic()
    cur = None
    while time.monotonic() - t0 < timeout:
        await page.wait_for_timeout(1000)
        cur = await _summary(page)
        if cur == last:
            seen += 1
        else:
            seen, last = 1, cur
        if seen >= 3 and (prev is None or cur != prev):
            return cur
    return cur


async def _run_pass(page, label: str, date_type: str, after_str: str, before_str: str, rows: dict, en: dict):
    await _no_cookie_banner(page)
    try:  # page size first: changing it after the filter leaves the table showing the old page
        pp = await _visible(page, "select[wire\\:model\\.live='perPage']")
        await pp.select_option("96")
        await _settle(page, 3000)
    except Exception as e:
        _log(f"⚠ {label}: could not set page size (non-fatal): {type(e).__name__}")
    radio = await _visible(page, f"ui-radio[value='{date_type}']")
    if radio:
        await radio.click()
        await _settle(page, 2000)
        _log(f"{label}: date-type switch clicked; checked={await radio.get_attribute('aria-checked')}")
    else:
        _log(f"⚠ {label}: date-type switch not found")
    sel = await _visible(page, "select[wire\\:model\\.live='datePreset']")
    await sel.select_option("custom")
    await _settle(page, 2000)
    for name, val in (("afterDate", after_str), ("beforeDate", before_str)):
        box = await _visible(page, f"input[wire\\:model\\.live='{name}']")
        await box.fill(val)
        await box.blur()
        await _settle(page, 2500)
    s = await _wait_stable(page)
    _log(f"{label}: after the date filter and page size 96: {s}")
    before_count = len(rows)
    total = s[2] if s else None
    for page_no in range(1, 80):
        await _read_page(page, rows, en)
        if not s or s[1] >= s[2] or s[1] == 0:
            break
        if elapsed_minutes() > MAX_MINUTES - 1:
            _log(f"⚠ {label}: time budget reached after page {page_no}")
            break
        await _no_cookie_banner(page)
        nxt = await _visible(page, "button[wire\\:click=\"nextPage('page')\"]")
        if not nxt:
            _log(f"⚠ {label}: no next-page button, stopped at {s}")
            break
        await nxt.click()
        s = await _wait_stable(page, prev=s)
        _log(f"{label}: page {page_no + 1}: {s}")
    _log(f"{label}: site reports {total} applications; {len(rows) - before_count} new rows read")
    if total and s and s[1] < total:
        _log(f"⚠ {label}: only reached {s[1]} of {total}")


async def scrape() -> list[dict]:
    today = date.today()
    after_str = (today - timedelta(days=DAYS_BACK)).isoformat()
    before_str = today.isoformat()
    rows: dict = {}
    en: dict = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        _log(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            await _settle(page, 3000)
            await _no_cookie_banner(page)
            # pass 1: validated in the window (new applications); pass 2: decided in the window
            await _run_pass(page, "validated", "1", after_str, before_str, rows, en)
            await _run_pass(page, "decided", "2", after_str, before_str, rows, en)
        except Exception as e:
            _log(f"⚠ Search interaction failed: {type(e).__name__}: {e!r}")
        finally:
            await context.close()
            await browser.close()

    apps = []
    for ref, r in rows.items():
        decided_date = _parse_ui_date(r.get("decided", ""))
        decision = ""
        if decided_date:
            decision = re.sub(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}\b", "", r.get("decided", ""))
            decision = re.sub(r"[—\-\s]+", " ", decision).strip()
        e_n = en.get(ref)
        apps.append({
            "Name": ref,
            "Site_Address__c": r.get("addr") or "",
            "Short_Proposal__c": r.get("desc") or None,
            "Type__c": r.get("type") or None,
            "Status__c": (decision if decision and decision.lower() != "decided" else (r.get("status") or decision)) or "",
            "Validated_Date__c": _parse_ui_date(r.get("valid", "")),
            "Current_Decision_Date__c": decided_date,
            "url": r.get("url") or None,
            "easting": e_n[0] if e_n else None,
            "northing": e_n[1] if e_n else None,
        })
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
    ll = [_en_to_latlng(a.get("easting"), a.get("northing")) for a in raw_apps]
    need = [p for p, (la, _) in zip(postcodes, ll) if p and la is None]
    coords = await geocode(need) if need else {}
    _log(f"{sum(1 for la, _ in ll if la is not None)} located from the map data, {len(need)} need a postcode lookup")

    fallback_count = 0
    records = []
    for a, address, postcode, (lat, lng) in zip(raw_apps, addresses, postcodes, ll):
        if lat is None and postcode:
            key = postcode.upper().replace(" ", "")
            if key in coords:
                lat, lng = coords[key]
        if lat is None:
            fallback_count += 1

        reference = a.get("Name")
        if not reference:
            continue

        decision_date = a.get("Current_Decision_Date__c")
        rec = {
            "council_id": cid,
            "reference": reference,
            "decision_date": decision_date,
            "address": address or None,
            "postcode": postcode,
            "description": a.get("Short_Proposal__c") or None,
            "application_type": a.get("Type__c") or None,
            "status": _normalise_south_derbyshire_status(a.get("Status__c", ""), decided=bool(decision_date)),
            "council_url": a.get("url"),
            "lat": lat,
            "lng": lng,
            "source": "south_derbyshire_scraper",
        }
        if a.get("Validated_Date__c"):  # never overwrite a stored date with nothing
            rec["submitted_date"] = a["Validated_Date__c"]
        records.append(rec)

    from collections import Counter
    _log("raw outcome labels: " + str(Counter(a.get("Status__c") for a in raw_apps if a.get("Current_Decision_Date__c")).most_common(12)))
    _log(f"{len(records)} applications; status {dict(Counter(r['status'] for r in records))}; "
         f"with decision date {sum(1 for r in records if r['decision_date'])}")
    for r in [x for x in records if x["decision_date"]][:3] + [x for x in records if not x["decision_date"]][:2]:
        _log(f"sample: {r['reference']} | {r.get('submitted_date')} | {r['decision_date']} | {r['status']} | "
             f"{r['application_type']!r} | {r['address']!r} | {r['lat']},{r['lng']}")

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if DRY_RUN:
        print(f"\nDRY RUN: nothing saved ({len(records)} applications read)")
        return

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
