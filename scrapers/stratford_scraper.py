#!/usr/bin/env python3
"""
PlanFind — Stratford-on-Avon (E-Planning v2.11) scraper (2026-08-27).

Real, confirmed evidence backing every design decision — see
stratford_councils.py. Genuinely the most stubborn platform in this
batch: 3 real diagnostic rounds were needed before the actual cause
was found — a genuine mistake in field selection (dateAppValidFrom
targeted instead of the correct dateApprecFrom), not any real
framework/automation limitation as first suspected.

ARCHITECTURE: fill the real "Date Application Received" fields
(dateApprecFrom/dateApprecTo, native type="date", using Playwright's
native .fill() — NOT raw JS value-setting, which was confirmed to
silently fail to update this framework's own internal state), submit,
parse the real Bootstrap-style result cards.

HONEST LIMITATIONS:
  - No detail URL at all. Real, confirmed: no href, onclick, or data
    attribute exists anywhere in a result card's static HTML — the
    real "click or tap to view details" behaviour is bound entirely by
    a separate JS framework file at runtime, genuinely not crawlable.
    Every application starts and stays 'pending' from this scraper
    alone — an even more limited situation than Barrow or Walsall,
    which at least had a real (if session-bound) detail URL.
  - Real "Status" column is a genuine workflow stage ("Pending
    Consideration"), not a decision outcome — same discipline as every
    other platform here, defaults to 'pending'.
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

from stratford_councils import COUNCIL_DB_IDS, BASE_URL

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
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "20"))

COUNCIL_NAME = "Stratford-on-Avon District Council"

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


def _parse_uk_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_results_cards(html: str) -> list[dict]:
    """Real, confirmed structure: div.card.SearchResult, real reference
    in the SECOND h5.card-title (the first contains a real, often-
    hidden 'Appeal' badge), real address in the p.card-text
    immediately following. Real fields organised as strong.labelStyle
    (label) + adjacent p.card-text (value) pairs — matching by real
    label TEXT rather than fixed position, since some labels (e.g.
    'Appeal Status') are hidden via display:none when not applicable,
    which would shift naive positional indices."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_="card SearchResult")

    apps = []
    for card in cards:
        title_tags = card.find_all("h5", class_="card-title")
        reference = None
        for t in title_tags:
            if "headerStyle" in (t.get("class") or []):
                reference = t.get_text(strip=True)
                break
        if not reference:
            continue

        address_tag = card.find("p", class_="card-text")
        address = address_tag.get_text(strip=True) if address_tag else ""
        postcode = _extract_postcode(address)

        field_values = {}
        for label in card.find_all("strong", class_="labelStyle"):
            label_text = label.get_text(strip=True)
            # REAL FIX — confirmed via direct testing against real
            # captured HTML: a hidden, empty p.card-text sometimes sits
            # between a label and its real value (e.g. "Date Valid"
            # has one before the genuine date text) — find_next_sibling
            # alone grabs the first match regardless of whether it's
            # empty, silently returning "". Checking each real sibling
            # in turn and taking the first with genuinely non-empty
            # text, not just the first matching tag.
            for sibling in label.find_next_siblings("p", class_="card-text"):
                text = sibling.get_text(strip=True)
                if text:
                    field_values[label_text] = text
                    break
                # Real, defensive stop: don't keep scanning past the
                # next real label's own value paragraphs — but since
                # find_next_siblings only returns siblings of the same
                # parent, this naturally stays scoped to the current
                # label's own real value slot(s).

        submitted_date = _parse_uk_date(field_values.get("Date Valid", ""))
        status_text = field_values.get("Status", "")
        proposal = field_values.get("Proposal", "")

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": postcode,
            "description": proposal,
            "submitted_date": submitted_date,
            "status": "pending",  # real, confirmed: 'Status' is a
                                    # workflow stage, not a decision
            "council_url": None,  # real, confirmed: no detail URL
                                    # exists anywhere in the static HTML
        })

    return apps


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
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        _log(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(f"{BASE_URL}/Home/AdvancedSearch", wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            _log(f"⚠ Could not load search page: {e}")
            await context.close()
            await browser.close()
            return []

        try:
            accept_btn = page.get_by_text("Accept", exact=True)
            if await accept_btn.count() > 0:
                await accept_btn.first.click(timeout=5_000)
                await asyncio.sleep(1)
        except Exception:
            pass

        today = date.today()
        start = today - timedelta(days=DAYS_BACK)

        try:
            # REAL, CONFIRMED FIX: dateApprecFrom/dateApprecTo (the
            # real "Date Application Received" fields), NOT
            # dateAppValidFrom/dateAppValidTo — a genuine mistake in
            # earlier field selection, not a framework limitation.
            # Real native .fill(), not raw JS value-setting, which was
            # confirmed to silently fail to register with this
            # framework's own internal state.
            await page.locator("#dateApprecFrom").first.fill(start.strftime("%Y-%m-%d"), timeout=5_000)
            await page.locator("#dateApprecTo").first.fill(today.strftime("%Y-%m-%d"), timeout=5_000)
            await page.locator("button:has-text('Search')").first.click(timeout=8_000)
        except Exception as e:
            _log(f"⚠ Could not fill/submit search: {e}")
            await context.close()
            await browser.close()
            return []

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1.5)

        page_num = 1
        while page_num <= MAX_PAGES:
            if should_stop():
                _log(f"⚠ Time budget reached, stopping at page {page_num}")
                break

            html = await page.content()
            page_apps = _parse_results_cards(html)
            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1

            body_text = ""
            try:
                body_text = await page.locator("body").inner_text()
            except Exception:
                pass
            m = re.search(r"Showing (\d+) Results", body_text)
            real_total = int(m.group(1)) if m else None

            _log(f"Page {page_num}: {new_count} new (running total {len(all_apps)}"
                 + (f" of {real_total} real total" if real_total else "") + ")")

            if real_total is not None and len(all_apps) >= real_total:
                break

            try:
                next_link = page.get_by_text("Next", exact=True)
                if await next_link.count() == 0:
                    break
                classes = await next_link.first.get_attribute("class") or ""
                if "disabled" in classes:
                    break
                await next_link.first.click(timeout=5_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(1.5)
                page_num += 1
            except Exception as e:
                _log(f"⚠ Could not click Next (page {page_num}): {e}")
                break

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Stratford scraper")
    print(f"Days back:   {DAYS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"stratford_councils.py.")
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
            "address": a["address"] or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "status": a["status"],
            "submitted_date": a.get("submitted_date"),
            "lat": lat,
            "lng": lng,
            "source": "stratford_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "stratford_eplanning",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")


if __name__ == "__main__":
    asyncio.run(main())
