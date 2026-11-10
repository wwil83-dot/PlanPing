#!/usr/bin/env python3
"""
PlanFind — Birmingham City Council scraper (2026-10-02).

Adapted directly from the proven, working broxbourne_scraper.py.
Broxbourne and Birmingham were both previously "never built at all".
Birmingham's URL uses a "/Northgate/" path segment rather than
Broxbourne's "/LPAssure/" — a real, genuinely different vendor name —
but a direct diagnostic confirmed every selector, id, and field name
matches exactly: the same outer toggle, the same confirmed onclick
handler for the Monthly list link, the same SelectedMonth dropdown,
and all 15 radio inputs identical down to their ids (SearchFor,
StatusOptions, SortOptions, MonthlyListStatus with
ValidatedThisMonth/DecidedThisMonth). "LPAssure" and "Northgate"
appear to be the same underlying platform under different branding.
The results table structure itself was not independently re-confirmed
for Birmingham specifically before building this — given everything
else matched exactly, it's used unchanged from Broxbourne's confirmed
structure, but this scraper's first real run should be checked
directly rather than assumed correct purely from the form-level match.

FLOW — independently confirmed for Birmingham (the toggle, the Monthly
list link's onclick, the SelectedMonth dropdown, and all 15 radio ids)
up through step 3 below. Steps 4-6 are inherited from Broxbourne's own
diagnostic, not independently re-verified for Birmingham specifically —
reasonable given every real id/name matched exactly on this platform,
but worth checking against this scraper's actual first real output
rather than assuming blind:
  1. Click the outer "Weekly / Monthly list" button (opens a panel;
     its inner Weekly/Monthly links start hidden until this is clicked).
  2. Click the inner "Monthly list" link — targeted by its exact,
     confirmed onclick handler
     (GetOnlinePlanningWeeklySearchView(false)), since a broad text
     match on "Monthly" accidentally re-clicked the outer toggle
     during diagnosis (it also contains the word "Monthly").
  3. Select a month from select#SelectedMonth (options are real month
     names, e.g. "September 2026").
  4. Check ONE of two mutually exclusive radios (same real name
     attribute, MonthlyListStatus): #ValidatedThisMonth or
     #DecidedThisMonth. On Broxbourne these showed genuinely different
     status vocabularies — Validated: "REGISTERED"; Decided: "FINAL
     DECISION", "WITHDRAWN" — so both searches are run, not just one,
     to capture new submissions and decision outcomes separately.
  5. Click the real "Search" button — on Broxbourne the page had THREE
     different buttons containing the text "Search" ("Map search",
     the real one, "Refine search"); the real one was identified by
     its nearest ancestor with a real id, 'ancWeeklyMonthlySearch'.
  6. Submitting a search replaces the whole page with results, which
     hid the original search panel on Broxbourne — re-running the
     same 5 steps is required for a second search, not just changing
     the radio and resubmitting.

RESULTS STRUCTURE — inherited unverified from Broxbourne, not yet
independently confirmed for Birmingham: table.table-striped
.OnlinePlanningWeeklyMonthlySort, tbody#divWeeklyMonthlySearchResultsForSorting,
each row with 6 <td> cells (reference linking to a real detail page,
status, development type, description, address, date received).
Longer descriptions are truncated with a "read more" link whose
onclick attribute contains the FULL, untruncated text
(DisplaySearchDescription('full text', 'Description')) — extracted
directly from there rather than settling for the truncated version.

HONEST LIMITATION: pagination exists on Broxbourne (79-97 results,
~20 shown per page by default) but Birmingham's real volume is unknown
until this scraper actually runs. Built with the same real page-size
fix (setting PagingParameters_PageSize to 50) and real numbered-
page-link clicking already proven on Broxbourne, plus the same-page-
detection safeguard already proven for West Northamptonshire, as a
fallback if either real control's own markup differs here.
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

BASE_URL = "https://planning.birmingham.gov.uk"
SEARCH_URL = f"{BASE_URL}/NECSWS/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch"
COUNCIL_NAME = "Birmingham City Council"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
MAX_MINUTES  = int(os.environ.get("MAX_MINUTES", "30"))
MONTHS_BACK  = int(os.environ.get("MONTHS_BACK", "2"))  # current + N-1 previous months
MAX_PAGES    = int(os.environ.get("MAX_PAGES", "20"))
BIRMINGHAM_COUNCIL_ID = int(os.environ.get("BIRMINGHAM_COUNCIL_ID", "0"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
# ONLY_SEARCHES: optional comma list like "July 2026/Validated,March 2026/Decided"; only those are run (for re-running failed ones)
ONLY_SEARCHES = {x.strip() for x in os.environ.get("ONLY_SEARCHES", "").split(",") if x.strip()}   # parse and report only; nothing written, no council id needed

START_TIME = time.monotonic()


def elapsed_minutes() -> float:
    return (time.monotonic() - START_TIME) / 60


def should_stop() -> bool:
    return elapsed_minutes() >= MAX_MINUTES - 2


def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b", text.upper())
    return m.group(1) if m else None


_STATUS_DIAGNOSED: set[str] = set()


def _normalise_status(s: str) -> str:
    if not s:
        return "pending"
    key = s.lower()
    if any(x in key for x in ("approv", "grant", "permit")):
        return "approved"
    if any(x in key for x in ("refus", "reject")):
        return "refused"
    if "withdraw" in key:
        return "withdrawn"
    if any(x in key for x in ("regist", "pending", "awaiting")):
        return "pending"
    # Confirmed real, genuinely ambiguous status from the "Decided"
    # search — reveals a decision exists without saying which outcome.
    if "final decision" in key or "decided" in key:
        return "pending"
    # An appeal being lodged doesn't reveal its eventual outcome.
    if "appeal lodged" in key:
        return "pending"
    if key not in _STATUS_DIAGNOSED:
        _STATUS_DIAGNOSED.add(key)
        print(f"    ⚠ STATUS DIAGNOSTIC: unrecognised status {s!r} — filed as 'pending'")
    return "pending"


def _parse_date(s: str) -> Optional[str]:
    """Real, confirmed format: 'DD Month YYYY' (e.g. '30 September 2026')."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def _real_month_options(n_months: int) -> list[str]:
    """Real, confirmed dropdown option format: 'September 2026'. Builds
    the current month plus n_months-1 previous ones."""
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(n_months):
        months.append(date(y, m, 1).strftime("%B %Y"))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


_ROW_STRUCTURE_DIAGNOSED = False


def _parse_results_page(html: str) -> list[dict]:
    """Real, confirmed structure from direct diagnostic tonight:
    table.OnlinePlanningWeeklyMonthlySort,
    tbody#divWeeklyMonthlySearchResultsForSorting, 6 <td> cells per row."""
    global _ROW_STRUCTURE_DIAGNOSED
    soup = BeautifulSoup(html, "html.parser")
    apps = []

    table = soup.find("table", class_="OnlinePlanningWeeklyMonthlySort")
    if not table:
        if not _ROW_STRUCTURE_DIAGNOSED:
            _ROW_STRUCTURE_DIAGNOSED = True
            print(f"    ⚠ ROW STRUCTURE DIAGNOSTIC: no real "
                  f"table.OnlinePlanningWeeklyMonthlySort found — real "
                  f"structure may have changed, or this search genuinely "
                  f"has zero results")
        return apps

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        ref_link = cells[0].find("a", href=True)
        reference = ""
        detail_url = None
        if ref_link:
            span = ref_link.find("span")
            reference = (span.get_text(strip=True) if span else ref_link.get_text(strip=True))
            href = ref_link.get("href")
            if href:
                detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        if not reference or len(reference) < 3:
            continue

        status_raw = cells[1].get_text(strip=True)
        dev_type = cells[2].get_text(strip=True)

        # Real, confirmed bonus: a "read more" link's onclick carries
        # the FULL, untruncated description — extracted directly
        # rather than settling for the truncated <td> text.
        description = cells[3].get_text(strip=True)
        read_more = cells[3].find("a", onclick=re.compile(r"DisplaySearchDescription"))
        if read_more:
            m = re.search(r"DisplaySearchDescription\('(.*?)',\s*'Description'\)",
                           read_more.get("onclick", ""), re.S)
            if m:
                description = m.group(1).strip()
            else:
                # Fall back to the truncated text with the "read more"
                # label stripped off, rather than keeping it glued on.
                description = re.sub(r"\s*read more\s*$", "", description).strip()

        address = cells[4].get_text(strip=True)
        date_raw = cells[5].get_text(strip=True)

        apps.append({
            "reference": reference,
            "address": address,
            "postcode": _extract_postcode(address),
            "description": description,
            "application_type": dev_type or "Planning",
            "status": _normalise_status(status_raw),
            "submitted_date": _parse_date(date_raw),
            "council_url": detail_url,
        })

    seen_refs: set[str] = set()
    deduped = []
    for a in apps:
        if a["reference"] not in seen_refs:
            seen_refs.add(a["reference"])
            deduped.append(a)
    return deduped


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


async def open_monthly_panel(page):
    """Real, confirmed 3-step flow to reach the month/status controls.
    Must be re-run before EACH search — confirmed via diagnostic that
    submitting a search replaces the page with results, hiding this
    panel entirely."""
    outer_toggle = page.locator("button:has-text('Weekly / Monthly')")
    await outer_toggle.click(timeout=5_000)
    await asyncio.sleep(1)

    monthly_link = page.locator("[onclick*='GetOnlinePlanningWeeklySearchView(false)']")
    await monthly_link.click(timeout=5_000, force=True)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1.5)


async def find_real_search_button(page):
    """Real, confirmed: the page has 3 buttons containing 'Search' text
    ('Map search', the real one, 'Refine search'). The real one is
    identified by its nearest ancestor with a real id,
    'ancWeeklyMonthlySearch' — confirmed via direct diagnostic, not a
    guess."""
    all_search_btns = page.locator("button", has_text="Search")
    for i in range(await all_search_btns.count()):
        btn = all_search_btns.nth(i)
        text = (await btn.text_content() or "").strip()
        parent_id = await btn.evaluate(
            "el => el.closest('[id]') ? el.closest('[id]').id : null"
        )
        if text == "Search" and parent_id == "ancWeeklyMonthlySearch":
            return btn
    return None


_DECISION_UNMAPPED: set = set()


def _birmingham_status(status: str, decision_type: str) -> str:
    """Status from Birmingham's own Status + DecisionType fields (both present in the results JSON)."""
    st, dt = (status or "").strip().lower(), (decision_type or "").strip().lower()
    if "withdraw" in st:
        return "withdrawn"
    if dt:
        if "refus" in dt:
            return "refused"
        if any(x in dt for x in ("approv", "no prior approval", "not required", "permission not required",
                                 "grant", "no objection", "certificate issued", "consent")):
            return "approved"
        if dt not in _DECISION_UNMAPPED:
            _DECISION_UNMAPPED.add(dt)
            print(f"    ⚠ UNMAPPED DECISION TYPE {decision_type!r} (status {status!r}): filed as 'closed'")
        return "closed"
    if st in ("registered", "appeal lodged", ""):
        return "pending"
    if "final decision" in st:
        return "closed"
    return _normalise_status(status)


def _iso_date(v) -> Optional[str]:
    return v[:10] if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v) else None


def _parse_results_json(html: str) -> list[dict]:
    """Birmingham returns EVERY result of a monthly search in one response: the hidden input hidSearchResultsJson
    holds {SearchResultsAll: [...all rows...]} (confirmed 2026-11-10: 456 decided / 210 validated for October,
    the visible table only shows 20 of them). No pagination needed."""
    import html as _html, json
    m = re.search(r'name="hidSearchResultsJson"\s+value="([^"]*)"', html)
    if not m:
        return []
    try:
        data = json.loads(_html.unescape(m.group(1)))
    except ValueError:
        return []
    rows = data.get("SearchResultsAll") or data.get("SearchResults") or []
    apps = []
    for r in rows:
        ref = (r.get("ApplicationId") or r.get("PrimaryKey") or "").strip()
        if len(ref) < 3:
            continue
        address = (r.get("DisplayAddressWithoutHTML") or "").strip()
        decision = _iso_date(r.get("DecisionDate"))
        status = _birmingham_status(r.get("Status"), r.get("DecisionType"))
        apps.append({
            "reference": ref,
            "address": address,
            "postcode": _extract_postcode(address),
            "description": (r.get("Description") or "").strip(),
            "application_type": (r.get("ApplicationType") or "").strip() or "Planning",
            "status": status,
            "submitted_date": _iso_date(r.get("DateRegistered")) or _iso_date(r.get("DateReceived")),   # registered = when it appears publicly; received can be months earlier
            "decision_date": decision if status != "pending" else None,
            "council_url": f"{BASE_URL}/NECSWS/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningOverview?applicationNumber="
                           + ref.replace("/", "%2F"),
        })
    return apps


async def scrape_month_status(page, month_label: str, status_id: str,
                              status_name: str) -> list[dict]:
    """One real monthly search (month + Validated/Decided). The portal is slow (10-15 s) and its panel sometimes
    fails to open, so the whole setup is retried; results are read from the JSON in the response."""
    for attempt in (1, 2, 3):
        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await open_monthly_panel(page)
            month_select = page.locator("select#SelectedMonth")
            if await month_select.count() == 0:
                raise RuntimeError("month select not on page")
            available = await month_select.locator("option").all_text_contents()
            if month_label not in available:
                print(f"    [{month_label}/{status_name}] month not yet offered by the council's own system "
                      f"(newest: {available[1] if len(available) > 1 else '?'}) — skipping, not an error")
                return []
            await month_select.select_option(label=month_label, timeout=5_000)
            await page.locator(f"input#{status_id}").check(timeout=5_000, force=True)
            btn = await find_real_search_button(page)
            if btn is None:
                raise RuntimeError("search button not found")
            await btn.click(timeout=5_000)
            html = ""
            for _ in range(36):                      # up to 3 minutes for the results
                await asyncio.sleep(5)
                html = await page.content()
                if 'name="hidSearchResultsJson"' in html:
                    break
            if 'name="hidSearchResultsJson"' not in html:
                diag = ""
                try:
                    r = LAST_RESP.get("resp")
                    if r is not None:
                        body = await r.text()
                        diag = f" | search response HTTP {r.status}, {len(body)} bytes, starts {re.sub(r'\\s+', ' ', body)[:260]!r}"
                    else:
                        diag = " | no search response was seen (the click may not have submitted)"
                    vis = await page.evaluate("() => [...document.querySelectorAll('.error, .errorRed, .field-validation-error')].filter(e => e.offsetWidth || e.offsetHeight).map(e => e.innerText.trim()).filter(Boolean).slice(0, 4).join(' / ')")
                    if vis:
                        diag += f" | page errors: {vis[:200]}"
                except Exception as _e:
                    diag += f" | (diagnostic failed: {type(_e).__name__})"
                raise RuntimeError("results never arrived (no results data on the page)" + diag)
            apps = _parse_results_json(html)
            m = re.search(r"(\d+)\s+Results", re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")))
            claimed = int(m.group(1)) if m else None
            note = "" if claimed is None or claimed == len(apps) else f"   ⚠ page says {claimed}"
            print(f"    [{month_label}/{status_name}] {len(apps)} applications{note}")
            if not apps and claimed:
                raise RuntimeError(f"page claims {claimed} results but none were read")
            return apps
        except Exception as e:
            print(f"    [{month_label}/{status_name}] ⚠ attempt {attempt} failed: {type(e).__name__}: {str(e)[:520]}")
            await asyncio.sleep(10 * attempt)
    FAILED.append(f"{month_label}/{status_name}")
    return []


FAILED: list = []
LAST_RESP: dict = {}


async def scrape() -> list[dict]:
    all_apps: list[dict] = []
    seen_refs: set[str] = set()

    months = _real_month_options(MONTHS_BACK)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()
        page.on("response", lambda r: LAST_RESP.update(resp=r) if "OnlinePlanningSearchResults" in r.url else None)

        for month_label in months:
            for status_id, status_name in [
                ("ValidatedThisMonth", "Validated"),
                ("DecidedThisMonth", "Decided"),
            ]:
                if ONLY_SEARCHES and f"{month_label}/{status_name}" not in ONLY_SEARCHES:
                    continue
                if should_stop():
                    print(f"⚠ Time budget reached, stopping before {month_label}/{status_name}")
                    break
                month_apps = await scrape_month_status(page, month_label, status_id, status_name)
                for a in month_apps:
                    if a["reference"] not in seen_refs:
                        seen_refs.add(a["reference"])
                        all_apps.append(a)
                    elif a["status"] != "pending":      # same application in both lists: keep the one with the decision
                        k = next(n for n, x in enumerate(all_apps) if x["reference"] == a["reference"])
                        if all_apps[k]["status"] == "pending":
                            all_apps[k] = a

        await context.close()
        await browser.close()

    return all_apps


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Birmingham City Council scraper")
    print(f"Months back: {MONTHS_BACK}")
    print(f"Budget:      {MAX_MINUTES} minutes")
    print(f"SUPABASE:    {'set' if SUPABASE_URL and SUPABASE_KEY else 'MISSING'}\n")

    print(f"DRY_RUN:     {DRY_RUN}")
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_KEY or not BIRMINGHAM_COUNCIL_ID):
        print("ERROR: SUPABASE_URL / SUPABASE_KEY / BIRMINGHAM_COUNCIL_ID not set.")
        sys.exit(1)

    raw_apps = await scrape()

    if DRY_RUN:
        from collections import Counter
        print(f"\nDRY_RUN: {len(raw_apps)} applications parsed, nothing written.")
        print("  by status:", dict(Counter(a["status"] for a in raw_apps)))
        print("  with postcode:", sum(1 for a in raw_apps if a.get("postcode")), " with decision_date:", sum(1 for a in raw_apps if a.get("decision_date")), " with submitted_date:", sum(1 for a in raw_apps if a.get("submitted_date")))
        for a in raw_apps[:10]:
            print("   ", a["reference"], "|", (a.get("address") or "")[:60], "|", a["status"], "|", a.get("submitted_date"), "|", (a.get("description") or "")[:50])
        return

    if not raw_apps:
        await _supa_patch_council(BIRMINGHAM_COUNCIL_ID, {
            "last_scraped_at": datetime.now(timezone.utc).isoformat()
        })
        print("\nNo real Birmingham applications found this run.")
        return

    postcodes = [a["postcode"] for a in raw_apps if a.get("postcode")]
    coords = await geocode(postcodes) if postcodes else {}
    if postcodes:
        print(f"Geocoding {len(postcodes)} postcodes…")

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
            "council_id": BIRMINGHAM_COUNCIL_ID,
            "reference": a["reference"],
            "address": a.get("address") or None,
            "postcode": a.get("postcode"),
            "description": a.get("description") or None,
            "application_type": a.get("application_type"),
            "status": a["status"],
            "submitted_date": a.get("submitted_date"),
            "decision_date": a.get("decision_date"),
            "council_url": a.get("council_url"),
            "lat": lat,
            "lng": lng,
            "source": "birmingham_scraper",
        })

    if fallback_count:
        print(f"No real coordinate for {fallback_count} apps (left null — no centroid fallback yet)")

    print(f"Upserting {len(records)} records with council_id={BIRMINGHAM_COUNCIL_ID}")
    ok = await _supa_upsert(records)
    if ok:
        print(f"✓ Saved {len(records)}")
        await _supa_patch_council(BIRMINGHAM_COUNCIL_ID, {
            "coverage_source": "birmingham_scraper",
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"\n{'=' * 50}")
    print(f"Finished in {elapsed_minutes():.1f} minutes")
    if FAILED:
        print(f"⚠ {len(FAILED)} search(es) failed after retries: {', '.join(FAILED)}")
    print(f"Applications saved: {len(records) if ok else 0}")


if __name__ == "__main__":
    asyncio.run(main())
