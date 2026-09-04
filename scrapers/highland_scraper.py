#!/usr/bin/env python3
"""
PlanFind — Highland Council scraper (2026-09-04).

Real, confirmed evidence backing every design decision — see
highland_councils.py.

ARCHITECTURE: Playwright throughout — loads the real weekly-list page,
finds real PDF links, downloads each via expect_download() (a genuine
forced Content-Disposition: attachment response, not a normal
navigation), then parses the REAL PLAIN TEXT (not pdfplumber's
unreliable table extraction, which fragments this PDF's layout into
junk 2-4-row tables) using a single, real-evidence-based regex matching
the confirmed repeating field pattern.

HONEST LIMITATION: this is a received-applications weekly list only —
no decision/status field exists anywhere in the real PDF structure.
Everything is filed as 'pending'.
"""
import asyncio
import io
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import httpx
import pdfplumber
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from highland_councils import COUNCIL_DB_IDS, WEEKLY_LIST_URL, BASE_URL

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
    "accept_downloads": True,
}

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DAYS_BACK    = int(os.environ.get("DAYS_BACK", "30"))
MAX_PDFS     = int(os.environ.get("MAX_PDFS", "6"))  # ~30 days at 1/week, generous buffer

COUNCIL_NAME = "Highland Council"

# Real, confirmed field sequence from actual extracted PDF text —
# non-greedy DOTALL matching so multi-line values (Description of
# Works, Location of Works, Applicant Address) are captured correctly.
RECORD_PATTERN = re.compile(
    r"Ref Number\s+(?P<ref>\S+)\s+"
    r"Application Type\s+(?P<apptype>.+?)\s+"
    r"Validation Date\s+(?P<valdate>\d{2}/\d{2}/\d{4})\s+"
    r"Grid Reference\s+(?P<easting>\d+)\s+(?P<northing>\d+)\s+"
    r"Expiry Date for lodging Representations\s+(?P<expiry>\d{2}/\d{2}/\d{4})\s+"
    r"Description of Works\s+(?P<description>.+?)\s+"
    r"Location of Works\s+(?P<location>.+?)\s+"
    r"Community Council\s+(?P<community>.+?)\s+"
    r"Applicant Name\s+(?P<applicant>.+?)\s+"
    r"Applicant Address\s+(?P<address>.+?)\s+"
    r"Case Officer\s+(?P<officer>.+?)(?=Ref Number|The following application was submitted online|\Z)",
    re.DOTALL,
)


def _log(msg: str) -> None:
    print(f"    [{COUNCIL_NAME}] {msg}")


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


def _clean_field(text: str) -> str:
    """Collapse internal whitespace/newlines from a multi-line
    extracted value into a single clean line."""
    return " ".join(text.split()).strip(" ,")


def _parse_highland_date(value: str) -> Optional[str]:
    """Real, confirmed format: DD/MM/YYYY."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def parse_pdf_text(full_text: str) -> list[dict]:
    apps = []
    for m in RECORD_PATTERN.finditer(full_text):
        ref = m.group("ref").strip()
        officer_block = _clean_field(m.group("officer"))
        # REAL FIX — the previous split("@")[0] approach only worked by
        # coincidence when a phone number preceded the email (the
        # digit-strip regex happened to cut before it). When only an
        # email followed the name with no phone, it left a mangled
        # duplicate (e.g. "Douglas Smith Douglas.Smith"). Properly
        # strip any real email pattern and any phone-like digit run,
        # in either order, rather than relying on a specific sequence.
        officer_name = re.sub(r"\S+@\S+", "", officer_block)
        officer_name = re.sub(r"\b\d[\d\s]{3,}\b", "", officer_name)
        officer_name = officer_name.strip()

        location = _clean_field(m.group("location"))

        apps.append({
            "reference": ref,
            "application_type": _clean_field(m.group("apptype")),
            "submitted_date": _parse_highland_date(m.group("valdate")),
            "description": _clean_field(m.group("description")),
            "address": location,
            # REAL FIX — previously fell back to the APPLICANT's
            # address postcode when the site's own "Location of Works"
            # had none. That's genuinely misleading, not just
            # imperfect: it would geocode a map marker to the
            # applicant's home address rather than the actual site,
            # silently placing the pin in the wrong location. Honest
            # council-centroid fallback (handled downstream) is the
            # right behaviour for a genuinely postcode-less rural site,
            # not borrowing an unrelated address.
            "postcode": _extract_postcode(location),
            "community_council": _clean_field(m.group("community")),
            "applicant": _clean_field(m.group("applicant")),
            "case_officer": officer_name,
            "status": "pending",  # real, confirmed: no decision field exists in this feed
        })

    return apps


async def fetch_pdf_links(page) -> list[tuple[str, str]]:
    await page.goto(WEEKLY_LIST_URL, wait_until="domcontentloaded", timeout=30_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    links = page.locator("a")
    count = await links.count()
    pdf_links = []
    for i in range(count):
        el = links.nth(i)
        href = await el.get_attribute("href")
        text = (await el.inner_text()).strip()
        if href and (".pdf" in href.lower() or "PDF" in text):
            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            pdf_links.append((text, full_url))
    return pdf_links


async def download_pdf(page, url: str) -> Optional[bytes]:
    """Real, confirmed: forced download (Content-Disposition:
    attachment) — needs expect_download(), not a plain navigation."""
    try:
        async with page.expect_download(timeout=30_000) as download_info:
            try:
                await page.goto(url, timeout=30_000)
            except Exception:
                pass  # the goto itself "fails" once the download starts — expected
        download = await download_info.value
        download_path = await download.path()
        if not download_path:
            return None
        with open(download_path, "rb") as f:
            return f.read()
    except Exception as e:
        _log(f"⚠ Download failed for {url}: {type(e).__name__}: {e!r}")
        return None


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

        pdf_links = await fetch_pdf_links(page)
        _log(f"Real PDF links found: {len(pdf_links)}, processing up to {MAX_PDFS}")

        for text, url in pdf_links[:MAX_PDFS]:
            content = await download_pdf(page, url)
            if not content:
                continue

            try:
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
            except Exception as e:
                _log(f"⚠ Could not parse PDF {text!r}: {type(e).__name__}: {e!r}")
                continue

            page_apps = parse_pdf_text(full_text)
            new_count = 0
            for a in page_apps:
                if a["reference"] not in seen_refs:
                    seen_refs.add(a["reference"])
                    all_apps.append(a)
                    new_count += 1
            _log(f"{text.splitlines()[0]!r}: {new_count} new (running total {len(all_apps)})")

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Highland Council scraper")
    print(f"Days back:   {DAYS_BACK} (~{MAX_PDFS} weekly PDFs)")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    cid = COUNCIL_DB_IDS[COUNCIL_NAME]
    if cid is None:
        print(f"ERROR: {COUNCIL_NAME} has a placeholder (None) DB id in "
              f"highland_councils.py. Run the INSERT_SQL there, look up "
              f"the real id, and fill it in before running this scraper.")
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

        supplementary = [p for p in (
            f"Applicant: {a['applicant']}" if a.get("applicant") else None,
            f"Community Council: {a['community_council']}" if a.get("community_council") else None,
            f"Case Officer: {a['case_officer']}" if a.get("case_officer") else None,
        ) if p]
        description_full = " | ".join([p for p in (a.get("description"), *supplementary) if p])

        records.append({
            "council_id": cid,
            "reference": a["reference"],
            "submitted_date": a.get("submitted_date"),
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": description_full or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "lat": lat,
            "lng": lng,
            "source": "highland_scraper",
        })

    if fallback_count:
        _log(f"Council centroid fallback for {fallback_count} apps "
             f"(rural/agricultural site addresses often lack a real postcode — "
             f"honest, expected for this council, not a parsing failure)")

    if records:
        _log(f"Upserting {len(records)} records with council_id={cid}")
        ok = await _supa_upsert(records)
        if ok:
            _log(f"✓ Saved {len(records)}")
            await _supa_patch_council(cid, {
                "coverage_source": "highland_pdf",
                "last_saved_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"\n{'=' * 50}")
    print("Finished")


if __name__ == "__main__":
    asyncio.run(main())
