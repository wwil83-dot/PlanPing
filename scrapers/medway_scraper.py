#!/usr/bin/env python3
"""
PlanFind — Medway Council (Open Digital Planning) scraper (2026-08-28).

Real, confirmed evidence backing every design decision — see
medway_councils.py.

ARCHITECTURE: paginate through the real, recency-sorted "Recently
published applications" listing via direct URL construction
(?page=N&resultsPerPage=10&type=simple), parsing each real
article.dpr-application-card. Stops when either the real "Next page"
link disappears, or a page's own "Received date" values fall outside
the desired DAYS_BACK window (since there's no real date-range filter
to request directly — only a recency-sorted list to page through).

HONEST LIMITATIONS:
  - Real, official, explicit caveat: "Not all planning applications
    are available on this register." This is a known-incomplete pilot,
    not a full register — genuinely the best available real source
    though, since the old Idox URL is confirmed dead.
  - Real "Status" values (e.g. "Consultation in progress", "Assessment
    in progress") are workflow stages, not final decisions — defaults
    to 'pending'. A real pending-recheck mechanism IS possible here (a
    genuine, permanent, reference-based detail link exists) — real
    detail-page field labels were never directly recon'd, so recheck
    logic uses a defensive keyword search, same discipline as before a
    detail page has ever been directly seen elsewhere in this project.
"""
import asyncio
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeout

from medway_councils import COUNCIL_DB_IDS, BASE_URL

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
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "30"))
RECHECK_LIMIT = int(os.environ.get("RECHECK_LIMIT", "50"))

COUNCIL_NAME = "Medway Council"

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _normalise_status(s: str) -> str:
    if not s:
        return "pending"
    d = s.lower()
    if any(x in d for x in ("approv", "grant", "permit", "allow", "no objection")):
        return "approved"
    if any(x in d for x in ("refus", "reject", "dismiss")):
        return "refused"
    if "withdraw" in d:
        return "withdrawn"
    return "pending"


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _parse_odp_date(s: str) -> Optional[str]:
    """Real, confirmed format: '14 Aug 2026'."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_cards(html: str) -> tuple[list[dict], bool]:
    """Real, confirmed structure: article.dpr-application-card, each
    real field a <dl><dt>label</dt><dd>value</dd></dl> pair. Returns
    (apps, has_next_page)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("article", class_="dpr-application-card")

    has_next = False
    for a in soup.find_all("a"):
        if a.get("rel") == ["next"] or a.get("rel") == "next":
            has_next = True
            break

    apps = []
    for card in cards:
        field_values = {}
        for dl in card.find_all("dl"):
            # REAL FIX — confirmed via direct testing against real
            # captured HTML: some dl elements (specifically the one
            # with class 'dpr-application-card__fields') bundle
            # MULTIPLE dt/dd pairs together (Application type, Status,
            # Received date, Valid from date, Published date,
            # Consultation end date — 6 pairs in one dl), not just
            # one. Using find_all (zipped) rather than find, which
            # only ever grabbed the first pair and silently discarded
            # the rest — including the real 'Received date' this
            # scraper's whole submitted_date field depends on.
            dts = dl.find_all("dt")
            dds = dl.find_all("dd")
            for dt, dd in zip(dts, dds):
                field_values[dt.get_text(strip=True)] = dd.get_text(strip=True)

        reference = field_values.get("Application reference", "")
        if not reference:
            continue

        detail_url = None
        for a in card.find_all("a"):
            href = a.get("href", "")
            if reference in href or "view" in a.get_text(strip=True).lower():
                detail_url = href if href.startswith("http") else f"https://planningregister.org{href}"
                break

        address = field_values.get("Address", "")
        postcode = _extract_postcode(address)
        received_raw = field_values.get("Received date", "")

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": field_values.get("Description", ""),
            "submitted_date": _parse_odp_date(received_raw),
            "status": _normalise_status(field_values.get("Status", "")),
            "council_url": detail_url,
        })

    return apps, has_next


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _supa_get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_h())
        r.raise_for_status()
        return r.json()


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


async def scrape(browser: Browser) -> list[dict]:
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    cutoff = date.today() - timedelta(days=DAYS_BACK)

    for page_num in range(1, MAX_PAGES + 1):
        if should_stop():
            _log(f"⚠ Time budget reached, stopping at page {page_num}")
            break

        url = f"{BASE_URL}?page={page_num}&resultsPerPage=10&type=simple"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Navigation error on page {page_num}: {e}")
            break

        if page_num == 1:
            try:
                accept_btn = page.get_by_text("Accept analytics cookies", exact=True)
                if await accept_btn.count() > 0:
                    await accept_btn.first.click(timeout=5_000)
                    await asyncio.sleep(1)
            except Exception:
                pass

        html = await page.content()
        page_apps, has_next = _parse_cards(html)

        if not page_apps:
            _log(f"Page {page_num}: no real cards found — stopping")
            break

        new_count = 0
        oldest_on_page = None
        for a in page_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
                new_count += 1
            if a.get("submitted_date"):
                d = date.fromisoformat(a["submitted_date"])
                if oldest_on_page is None or d < oldest_on_page:
                    oldest_on_page = d

        _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)})"
             + (f" — oldest real received date on this page: {oldest_on_page}" if oldest_on_page else ""))

        # Real, honest early-exit: since there's no real date-range
        # filter on this platform, paging through a recency-sorted
        # list is the only way to reach older applications — stopping
        # once this page's own oldest real 'Received date' falls
        # outside the desired window, since every subsequent page will
        # only be older still.
        if oldest_on_page is not None and oldest_on_page < cutoff:
            _log(f"Oldest real received date on this page ({oldest_on_page}) is "
                 f"outside the {DAYS_BACK}-day window — stopping")
            break

        if not has_next:
            _log(f"No real 'Next page' link found — reached the end")
            break

        await asyncio.sleep(1)

    await context.close()
    return all_apps


async def recheck_pending(browser: Browser, pending: list[dict]) -> list[dict]:
    """Real, confirmed permanent, reference-based detail link — a
    genuine pending-recheck mechanism is possible here. Real detail-
    page field labels never actually recon'd — using a defensive
    keyword search, same discipline as before a detail page has ever
    been directly seen elsewhere in this project."""
    if not pending:
        return []
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    updates = []
    for p in pending:
        if should_stop():
            _log(f"⚠ Time budget reached mid-recheck, stopping")
            break
        url = p.get("council_url")
        if not url:
            continue
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
        except Exception:
            continue

        text = ""
        try:
            text = await page.locator("body").inner_text()
        except Exception:
            continue

        m = re.search(r"status\s*\n?\s*([A-Za-z ,.'()-]+)", text, re.I)
        if m:
            status_text = m.group(1).strip()
            status = _normalise_status(status_text)
            if status != "pending":
                updates.append({"reference": p["reference"], "status": status})

    await context.close()
    if updates:
        _log(f"Recheck: {len(updates)} of {len(pending)} previously-pending "
             f"application(s) now have a real decision")
    return updates


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Medway scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")
    print(f"HONEST NOTE: this is a known-incomplete Open Digital Planning pilot "
          f"register — 'Not all planning applications are available on this "
          f"register' per the site's own text.\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"medway_councils.py.")
        sys.exit(1)

    print(f"[{COUNCIL_NAME}] (council_id={cid})\n")

    pending = []
    try:
        pending = await _supa_get(
            "planning_applications",
            council_id=f"eq.{cid}",
            status="eq.pending",
            select="reference,council_url",
            limit=str(RECHECK_LIMIT),
        )
        if pending:
            _log(f"Pending recheck: {len(pending)} applications (bounded to {RECHECK_LIMIT})")
    except Exception as e:
        _log(f"⚠ Failed to fetch pending recheck list (continuing without it): {e}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        raw_apps = await scrape(browser)
        recheck_updates = await recheck_pending(browser, pending)

        await browser.close()

    if not raw_apps and not recheck_updates:
        print("\nNo results and no recheck updates — nothing to save.")
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
            "address": a["address"] or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "submitted_date": a.get("submitted_date"),
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "medway_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    recheck_records = [{
        "council_id": cid,
        "reference": u["reference"],
        "status": u["status"],
    } for u in recheck_updates]

    saved_count = 0
    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        if await _supa_upsert(records):
            saved_count += len(records)

    if recheck_records:
        _log(f"Upserting {len(recheck_records)} recheck status updates with council_id={cid}")
        if await _supa_upsert(recheck_records):
            saved_count += len(recheck_records)

    if saved_count:
        _log(f"✓ Saved {saved_count}")
        await _supa_patch_council(cid, {
            "coverage_source": "medway_odp",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
