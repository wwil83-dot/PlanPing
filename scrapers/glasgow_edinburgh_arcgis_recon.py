#!/usr/bin/env python3
"""
PlanFind — Glasgow/Edinburgh ArcGIS FeatureServer recon (2026-09-03).

Real find from the user's own browsing: Glasgow publishes a "Major and
Significant Planning Applications Dashboard" via ArcGIS Experience
Builder (experience.arcgis.com/experience/158560dc6db447cc9eeb4a40ca8c1e79),
showing real structured data (721 Major, 2,273 Significant Local, 1.1k
Screening/Scoping/PAN applications) with real references, dates,
addresses, descriptions.

A broader web search for this pattern also surfaced OTHER UK councils
(Tonbridge & Malling, City of London, Fife) publishing planning
application data via public ArcGIS FeatureServer/MapServer REST APIs —
real, queryable JSON/GeoJSON endpoints with structured fields
(reference, address, proposal, decision text, actual site geometry),
no scraping resistance at all. This may be a genuinely valuable
platform category beyond just Glasgow.

This recon:
  1. Fetches Glasgow's Experience Builder app config directly via the
     ArcGIS REST API (item data endpoint) — Experience Builder apps
     store their widget configuration as JSON, which should reveal the
     underlying FeatureServer/MapServer URLs the dashboard's widgets
     actually query.
  2. Loads Edinburgh's planning-weekly-lists page, finds the real "View
     the applications received and decided map" button, and follows it
     to check whether IT is also ArcGIS-backed with a similarly
     discoverable FeatureServer.
  3. If a real FeatureServer URL is found for either, queries it
     directly for a small sample of real records to confirm the field
     schema and data quality before any scraper gets built around it.
"""
import asyncio
import json
import re
from datetime import datetime, timezone

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

GLASGOW_EXPERIENCE_ITEM_ID = "158560dc6db447cc9eeb4a40ca8c1e79"
EDINBURGH_WEEKLY_LISTS_URL = "https://www.edinburgh.gov.uk/planning-applications-1/planning-weekly-lists"

# Real, confirmed-real FeatureServer URL pattern from this exact
# search — used here just to double-check the general approach works
# before trusting it for Glasgow specifically.
KNOWN_REAL_EXAMPLE = "https://mapsat.tmbc.gov.uk/server/rest/services/Agile_Maps/Planning_Applications_dashboard/MapServer?f=json"


def find_urls_in_json(obj, found=None):
    """Recursively search a parsed JSON structure for anything that
    looks like an ArcGIS Feature/MapServer URL."""
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for v in obj.values():
            find_urls_in_json(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_urls_in_json(v, found)
    elif isinstance(obj, str):
        if re.search(r"(FeatureServer|MapServer)", obj, re.IGNORECASE):
            found.add(obj)
    return found


async def recon_glasgow():
    print(f"\n{'=' * 70}")
    print("RECON: Glasgow Major/Significant Planning Applications Dashboard")
    print("=" * 70)

    url = f"https://www.arcgis.com/sharing/rest/content/items/{GLASGOW_EXPERIENCE_ITEM_ID}/data?f=json"
    print(f"Fetching Experience Builder config: {url}")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(url)
            print(f"  Real HTTP status: {r.status_code}")
            if r.status_code == 200:
                try:
                    data = r.json()
                    found_urls = find_urls_in_json(data)
                    print(f"  Real FeatureServer/MapServer URLs found in config: {len(found_urls)}")
                    for u in found_urls:
                        print(f"    {u}")
                    with open("/tmp/glasgow_experience_config.json", "w") as f:
                        json.dump(data, f, indent=2)
                    print("  Saved: /tmp/glasgow_experience_config.json")

                    # Test each found URL directly
                    for u in found_urls:
                        await test_featureserver(client, u)
                except json.JSONDecodeError:
                    print(f"  ⚠ Response wasn't valid JSON. First 500 chars: {r.text[:500]!r}")
            else:
                print(f"  ⚠ Non-200 response. First 500 chars: {r.text[:500]!r}")
        except Exception as e:
            print(f"  ⚠ Request failed: {type(e).__name__}: {e!r}")


async def test_featureserver(client: httpx.AsyncClient, base_url: str):
    """Given a real FeatureServer/MapServer base URL, try querying
    layer 0 for a small real sample to confirm the schema."""
    print(f"\n  Testing real FeatureServer: {base_url}")
    clean_base = base_url.split("?")[0].rstrip("/")
    query_url = f"{clean_base}/0/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": "3",
    }
    try:
        r = await client.get(query_url, params=params)
        print(f"    Real HTTP status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                print(f"    ⚠ Real API error: {data['error']}")
            else:
                features = data.get("features", [])
                print(f"    Real sample records returned: {len(features)}")
                for feat in features[:2]:
                    print(f"      {feat.get('attributes', {})}")
    except Exception as e:
        print(f"    ⚠ Query failed: {type(e).__name__}: {e!r}")


async def recon_edinburgh(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Edinburgh 'View the applications received and decided map'")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(EDINBURGH_WEEKLY_LISTS_URL, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        map_button = page.get_by_text("View the applications received and decided map", exact=False)
        count = await map_button.count()
        print(f"  Found {count} matching button/link")

        if count > 0:
            href = await map_button.first.get_attribute("href")
            if href:
                print(f"  Real href: {href}")
            else:
                # Might be a JS-driven button rather than a plain link —
                # try clicking it and see where it navigates
                try:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                        await map_button.first.click()
                    print(f"  Real final URL after click: {page.url}")
                except PlaywrightTimeout:
                    print(f"  ⚠ Click didn't trigger a navigation — may open in a new "
                          f"tab/modal, or be a non-link element")
        else:
            print("  ⚠ Button not found by that exact text — real page text may differ")
            body_text = (await page.locator("body").inner_text())[:1000]
            print(f"  Real body text (first 1000 chars): {body_text!r}")

    except Exception as e:
        print(f"  ⚠ Error: {type(e).__name__}: {e!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Glasgow/Edinburgh ArcGIS recon\n")

    await recon_glasgow()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"\nChromium launched: {browser.version}")

        await recon_edinburgh(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
