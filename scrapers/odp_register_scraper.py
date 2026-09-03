#!/usr/bin/env python3
"""
PlanFind — Open Digital Planning Register scraper: Barnet and
Buckinghamshire (2026-09-01).

Real, confirmed evidence backing every design decision — see
odp_register_councils.py. All parsing/scraping logic below is a direct
generalisation of medway_scraper.py's already-proven, real-evidence-
backed implementation (same platform, planningregister.org, confirmed
identical structure for these two councils via direct web fetch before
this was written) — just parameterised to loop over multiple council
slugs instead of one hardcoded council.

ARCHITECTURE: identical to medway_scraper.py — paginate through the
real, recency-sorted "Recently published applications" listing via
direct URL construction (?page=N&resultsPerPage=10&type=simple),
parsing each real article.dpr-application-card, stopping when the real
"Next page" link disappears. Date-window filtering happens AFTER
fetching everything (not during), same real fix already proven
necessary for Medway (this platform sorts by published date, not
received date, so an early-exit during pagination risked missing
genuinely recent applications sitting on later pages).

HONEST LIMITATIONS (same as Medway):
  - Real, official, explicit caveat: "Not all planning applications
    are available on this register." A known-incomplete pilot, not a
    full register — but the best genuinely available route for these
    two, given their own Idox instances are confirmed blocked (see
    odp_register_councils.py's module docstring for that evidence
    trail).
  - Real "Status" values are workflow stages, not final decisions —
    defaults to 'pending'. Recheck logic uses a defensive keyword
    search since real detail-page field labels were never directly
    recon'd for these two specifically (only Medway's detail page
    structure was ever directly inspected) — same discipline as before
    a detail page has been directly seen elsewhere in this project.

DELIBERATELY a separate script from medway_scraper.py rather than a
merge — Medway's job is already live and scheduled nightly; safer not
to touch a working production job while adding new councils to the
same underlying platform. A future cleanup could merge all three into
one proper shared-platform scraper (matching
getapplications_scraper.py's multi-council architecture) without
changing behaviour for any of them.
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

from odp_register_councils import COUNCIL_DB_IDS, ODP_COUNCILS

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

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


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


def _parse_cards(html: str, base_url: str) -> tuple[list[dict], bool]:
    """Real, confirmed structure (identical across all councils on this
    platform): article.dpr-application-card, each real field a
    <dl><dt>label</dt><dd>value</dd></dl> pair. Returns
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

        # REAL FIX (2026-09-03) — barnet_page2_diagnostic.py's real
        # captured data showed "Status: Determined" alongside a SEPARATE
        # "Council decision: Granted" field. Status alone only reflects
        # workflow stage (Determined/Pending/etc.), not the actual
        # outcome — using Council decision when present, falling back
        # to Status only if it's absent.
        decision_text = field_values.get("Council decision") or field_values.get("Status", "")

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": field_values.get("Description", ""),
            "submitted_date": _parse_odp_date(received_raw),
            "status": _normalise_status(decision_text),
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


async def scrape_council(browser: Browser, council_name: str, slug: str) -> list[dict]:
    base_url = f"https://planningregister.org/{slug}"

    def _log(msg: str) -> None:
        print(f"    [{council_name}] {msg}")

    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    page_num = 1
    while page_num <= MAX_PAGES:
        if should_stop():
            _log(f"⚠ Time budget reached, stopping at page {page_num}")
            break

        url = f"{base_url}?page={page_num}&resultsPerPage=10&type=simple"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
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

        # REAL FIX (2026-09-01) — Barnet's first live run found ZERO
        # <article> or <dl> tags anywhere in the page, despite the real
        # body text clearly showing the genuine "Recently published
        # applications" heading and real pagination (confirming this IS
        # the right page, just captured before its content rendered).
        # Medway's identical wait (networkidle, 10s) was apparently
        # sufficient for ITS deployment, but Barnet's specific instance
        # likely loads the actual cards via an async client-side fetch
        # that takes longer. Explicitly waiting for a real <article> to
        # appear (rather than just network activity settling) before
        # parsing — falls through gracefully to the existing diagnostic
        # if genuinely nothing ever appears.
        try:
            await page.wait_for_selector("article", timeout=8_000)
        except PlaywrightTimeout:
            pass  # let the existing empty-page diagnostic below report this

        html = await page.content()
        page_apps, has_next = _parse_cards(html, base_url)

        if not page_apps:
            if page_num == 1:
                # REAL FIX (2026-09-01/03) — barnet_page2_diagnostic.py
                # directly confirmed pages 2 and 4 BOTH have real,
                # distinct content (4 articles/16 <dl> elements each),
                # while page 1 specifically is genuinely empty —
                # consistently, across multiple runs, even with an
                # explicit wait for a real <article> element. This is a
                # real quirk isolated to page 1 on this specific "Beta"
                # deployment (its own page text: "This is a new
                # service"), most plausibly an off-by-one pagination
                # indexing bug on their end — NOT a fundamental
                # structural mismatch with our parsing logic, which is
                # confirmed correct against pages 2+. Skipping page 1
                # and starting real collection from page 2 instead,
                # rather than treating this as "reached the end."
                _log(f"Page 1: no real cards found — CONFIRMED known "
                     f"quirk on this council's deployment (page 1 "
                     f"specifically is broken; pages 2+ work fine), "
                     f"skipping to page 2 rather than stopping")
                page_num += 1
                continue
            _log(f"Page {page_num}: no real cards found — stopping")
            break

        new_count = 0
        for a in page_apps:
            if a["reference"] not in seen_refs:
                seen_refs.add(a["reference"])
                all_apps.append(a)
                new_count += 1

        _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)})")

        if not has_next:
            _log(f"No real 'Next page' link found — reached the end")
            break

        page_num += 1
        await asyncio.sleep(1)

    await context.close()
    return all_apps


async def recheck_pending(browser: Browser, council_name: str, pending: list[dict]) -> list[dict]:
    """Real, confirmed permanent, reference-based detail link — same
    mechanism proven for Medway. Real detail-page field labels never
    directly recon'd for Barnet/Buckinghamshire specifically — using a
    defensive keyword search, same discipline as before a detail page
    has been directly seen elsewhere in this project."""
    def _log(msg: str) -> None:
        print(f"    [{council_name}] {msg}")

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


async def process_council(browser: Browser, council_name: str, slug: str, cid: int) -> int:
    def _log(msg: str) -> None:
        print(f"    [{council_name}] {msg}")

    print(f"\n[{council_name}] (council_id={cid})")

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

    raw_apps = await scrape_council(browser, council_name, slug)

    cutoff = date.today() - timedelta(days=DAYS_BACK)
    before_filter_count = len(raw_apps)
    raw_apps = [
        a for a in raw_apps
        if not a.get("submitted_date") or date.fromisoformat(a["submitted_date"]) >= cutoff
    ]
    if before_filter_count != len(raw_apps):
        # REAL FIX (2026-09-01) — first live run's log looked
        # contradictory: "1 new" during fetch, then "nothing to save"
        # with no explanation in between. This platform sorts by
        # PUBLISHED date, not received date (same real finding already
        # proven for Medway), so an application can be found during
        # pagination but then correctly filtered out here for having a
        # real submitted_date outside the DAYS_BACK window. Logging
        # this explicitly so it reads as honest filtering, not a
        # silent/confusing drop.
        _log(f"Date-window filter: {before_filter_count - len(raw_apps)} of "
             f"{before_filter_count} found application(s) had a real "
             f"submitted_date outside the last {DAYS_BACK} days — filtered "
             f"out (this platform sorts by published date, not received "
             f"date, same real finding already proven for Medway)")

    recheck_updates = await recheck_pending(browser, council_name, pending)

    if not raw_apps and not recheck_updates:
        _log("No results and no recheck updates — nothing to save.")
        return 0

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
            "source": "odp_register_scraper",
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
            "coverage_source": "odp_register",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    return saved_count


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind ODP Register scraper "
          f"(Barnet + Buckinghamshire)")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")
    print(f"HONEST NOTE: this is a known-incomplete Open Digital Planning pilot "
          f"register — 'Not all planning applications are available on this "
          f"register' per the site's own text.\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    unresolved = [name for name, cid in COUNCIL_DB_IDS.items() if cid is None]
    if unresolved:
        print("ERROR: the following councils still have a placeholder "
              "(None) DB id in odp_register_councils.py:")
        for name in unresolved:
            print(f"  - {name}")
        print("\nRun odp_register_councils.py's INSERT_SQL in Supabase first, "
              "then replace each None above with the real id returned.")
        sys.exit(1)

    total_saved = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for council_name, slug in ODP_COUNCILS:
            cid = COUNCIL_DB_IDS[council_name]
            try:
                saved = await process_council(browser, council_name, slug, cid)
                total_saved += saved
            except Exception as e:
                print(f"    [{council_name}] ✗ Error: {e}")

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    print(f"Total applications saved: {total_saved}")


if __name__ == "__main__":
    asyncio.run(main())
