#!/usr/bin/env python3
"""
PlanFind — ScraperAPI test (2026-08-16).

PURPOSE: All three residential proxy providers checked so far (Bright
Data, Oxylabs, Decodo) explicitly restrict government-category sites
behind some form of KYC/ID verification. A secondhand AI-generated
summary claimed ScraperAPI does not universally block .gov sites —
worth treating as an unverified claim, not fact, and testing directly
against real evidence rather than relying on it. Uses the same three
confirmed-blocked councils as the Bright Data test (round 2), for a
genuinely fair, direct comparison.

Also tests session persistence via ScraperAPI's own session_number
parameter (explicitly documented to keep the same IP across requests
for 15 minutes) — directly relevant to our session-dependent Idox
councils, and a real, different mechanism than what was tested for
Bright Data.

Credentials read from environment variables — never hardcoded, matching
the same convention already used for SUPABASE_URL/SUPABASE_KEY
throughout this whole project.
"""
import os
import sys
import time
import requests

API_KEY = os.environ.get("SCRAPERAPI_KEY", "")
API_ENDPOINT = "https://api.scraperapi.com"
# A fresh, genuinely random session number each run, not a hardcoded
# value that could carry a stale/expired session from an earlier run.
SESSION_NUMBER = int(time.time())

TARGETS = [
    ("Aberdeenshire Council",
     "https://upa.aberdeenshire.gov.uk/online-applications",
     "search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application",
     "monthlyListResults.do?action=firstPage"),
    ("Babergh District Council",
     "https://planning.baberghmidsuffolk.gov.uk/online-applications",
     "search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application",
     "monthlyListResults.do?action=firstPage"),
    ("Argyll and Bute Council",
     "https://publicaccess.argyll-bute.gov.uk/online-applications",
     "search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application",
     "monthlyListResults.do?action=firstPage"),
]

RESULTS_CONTAINER_MARKERS = [
    'ul.searchresults', 'id="searchresults"', 'searchresultscontainer',
    'class="searchresults"', 'searchresultsform', 'no-results',
]
WAF_BLOCK_MARKERS = [
    "429 too many requests", "too many requests", "unusual traffic",
    "access denied", "cloudflare",
]
RESTRICTION_MARKERS = [
    "kyc", "not permitted", "restricted", "not available", "blocked domain",
]
# ADDED (2026-08-16) — real evidence of an actual application listing,
# not just a page that LOADED. A page title matching "Monthly List"
# alone isn't proof real data came through — this looks for the actual
# shape of Idox application rows (reference number patterns, the
# specific column headers Idox monthly-list pages always show).
REAL_DATA_MARKERS = [
    "application type", "date received", "ref. no", "decision",
]


def slug(name: str) -> str:
    s = name.lower().replace(" ", "_").replace(".", "")
    return "".join(c for c in s if c.isascii() and (c.isalnum() or c == "_"))


def scrape_request(target_url: str, label: str, extra_params: dict = None,
                    save_html: bool = False) -> dict:
    """One real call to ScraperAPI — returns honest, direct diagnostics."""
    print(f"\n{'-' * 70}")
    print(f"{label}")
    print(f"Target URL: {target_url}")
    print("-" * 70)

    if not API_KEY:
        print("  ⚠ SCRAPERAPI_KEY not set — cannot test.")
        return {"error": "missing credentials"}

    # BUG FIX (2026-08-16) — the hardcoded session_number="101" risked
    # reusing a stale/expired session across separate script runs
    # (sessions expire 15 minutes after last use per ScraperAPI's own
    # docs). A fresh, genuinely random session number each run avoids
    # that specific confound when comparing results across runs.
    params = {"api_key": API_KEY, "url": target_url, "premium": "true",
              "session_number": str(SESSION_NUMBER)}
    if extra_params:
        params.update(extra_params)

    try:
        response = requests.get(API_ENDPOINT, params=params, timeout=70)
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ Request itself failed: {e}")
        return {"error": str(e)}

    print(f"  HTTP status from ScraperAPI: {response.status_code}")
    print(f"  Response headers of note:")
    for k in response.headers:
        if k.lower().startswith("sa-") or "scraperapi" in k.lower():
            print(f"    {k}: {response.headers[k]}")

    if response.status_code != 200:
        print(f"  Response body (first 500 chars): {response.text[:500]!r}")
        return {"error": f"non-200: {response.status_code}", "body": response.text[:2000]}

    html = response.text
    html_lower = html.lower()

    if len(html) == 0:
        print(f"  ⚠ Real content length: 0 chars — genuinely empty body despite HTTP 200.")
        return {"status": 200, "length": 0, "empty_200": True}

    has_results_container = any(m.lower() in html_lower for m in RESULTS_CONTAINER_MARKERS)
    waf_hits = [m for m in WAF_BLOCK_MARKERS if m in html_lower]
    restriction_hits = [m for m in RESTRICTION_MARKERS if m in html_lower]
    real_data_hits = [m for m in REAL_DATA_MARKERS if m in html_lower]

    print(f"  Real content length: {len(html):,} chars")
    print(f"  Real page title: {html[html_lower.find('<title>')+7:html_lower.find('</title>')] if '<title>' in html_lower else '(no title tag found)'}")
    print(f"  Results container found: {'YES' if has_results_container else 'NO'}")
    print(f"  Real Idox data markers found: {real_data_hits if real_data_hits else 'NONE — genuinely worth doubting this is real application data'}")
    print(f"  Restriction/policy markers found: {restriction_hits if restriction_hits else 'none'}")
    print(f"  WAF/block-page markers found: {waf_hits if waf_hits else 'none'}")

    # ADDED (2026-08-16) — real, direct extraction of the monthly-list
    # form's actual structure. Our own production scraper has to try
    # SEVERAL candidate selectors for the "date received" radio button
    # (dc, DC, dv, DV, dateReceived — genuinely varies by council), so
    # rather than guess which applies to these three specific councils,
    # extract the real field names directly from the real HTML we
    # already have, using BeautifulSoup rather than fragile hand-rolled
    # regex for something structural like this.
    form_info = None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", id="monthlyListForm") or soup.find("form")
        if form:
            fields = []
            for el in form.find_all(["input", "select"]):
                fields.append({
                    "tag": el.name,
                    "type": el.get("type", ""),
                    "name": el.get("name", ""),
                    "id": el.get("id", ""),
                    "value": el.get("value", ""),
                })
            form_info = {
                "action": form.get("action", "(none — submits to current URL)"),
                "method": form.get("method", "GET"),
                "fields": fields,
            }
            print(f"  Real form found — action={form_info['action']!r}, method={form_info['method']!r}")
            print(f"  Real form fields:")
            for f in fields:
                print(f"    <{f['tag']}> type={f['type']!r} name={f['name']!r} "
                      f"id={f['id']!r} value={f['value']!r}")
        else:
            print(f"  ⚠ No <form> element found at all in this response.")
    except ImportError:
        print(f"  ⚠ BeautifulSoup not installed — skipping form structure extraction.")

    # Much bigger snippet than before (400 chars only ever showed <head>)
    # — enough to actually see whether real application rows are present
    snippet = " ".join(html.split())[:3000]
    print(f"  Content snippet (first 3000 chars of visible text): {snippet!r}")

    if save_html:
        path = f"/tmp/scraperapi_{slug(label)}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Full HTML saved: {path} ({len(html):,} chars) — download as a workflow artifact if needed")

    return {
        "status": response.status_code,
        "length": len(html),
        "has_results_container": has_results_container,
        "waf_hits": waf_hits,
        "restriction_hits": restriction_hits,
        "real_data_hits": real_data_hits,
        "form_info": form_info,
    }


def submit_monthly_list_form(name: str, base_url: str, form_path: str) -> dict:
    """The real, two-step flow now that we know the actual form structure:
    1. Fetch the form page (as already proven to work), extract a real,
       fresh CSRF token from it.
    2. POST the real form fields — _csrf, month, dateType, searchType —
       to the form's own action URL, using the SAME session_number so
       (hopefully) the cookies/session that issued the CSRF token carry
       over to the POST. This is the genuinely uncertain part, worth
       testing directly rather than assuming either way."""
    print(f"\n{'#' * 70}")
    print(f"# {name} — real two-step form submission")
    print("#" * 70)

    form_url = f"{base_url}/{form_path}"
    get_result = scrape_request(form_url, f"{name} — Step 1: fetch form", save_html=False)

    if not get_result.get("form_info"):
        print(f"  ⚠ No form info extracted — cannot proceed to Step 2 for {name}.")
        return {"error": "no form info from step 1"}

    form_info = get_result["form_info"]
    csrf_token = None
    for field in form_info["fields"]:
        if field["name"] == "_csrf":
            csrf_token = field["value"]
            break

    if not csrf_token:
        print(f"  ⚠ No _csrf token found in the extracted form — cannot proceed.")
        return {"error": "no csrf token"}

    print(f"  Real CSRF token extracted: {csrf_token}")

    action = form_info["action"]
    if action.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        post_target = f"{parsed.scheme}://{parsed.netloc}{action}"
    else:
        post_target = action

    # Real form data, using the actual field names/values found in Step 1
    post_data = {
        "_csrf": csrf_token,
        "month": "0",  # current month, matching month_index=0 convention
        "dateType": "DC_Validated",
        "searchType": "Application",
    }

    print(f"\n{'-' * 70}")
    print(f"{name} — Step 2: POST real form data to {post_target}")
    print(f"POST body: {post_data}")
    print("-" * 70)

    params = {"api_key": API_KEY, "url": post_target, "premium": "true",
              "session_number": str(SESSION_NUMBER)}
    try:
        response = requests.post(API_ENDPOINT, params=params, data=post_data, timeout=70)
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ POST request itself failed: {e}")
        return {"error": str(e)}

    print(f"  HTTP status from ScraperAPI: {response.status_code}")
    for k in response.headers:
        if k.lower().startswith("sa-"):
            print(f"    {k}: {response.headers[k]}")

    if response.status_code != 200:
        print(f"  Response body (first 500 chars): {response.text[:500]!r}")
        return {"error": f"non-200: {response.status_code}"}

    html = response.text
    html_lower = html.lower()
    real_data_hits = [m for m in REAL_DATA_MARKERS if m in html_lower]
    has_results_container = any(m.lower() in html_lower for m in RESULTS_CONTAINER_MARKERS)

    print(f"  Real content length: {len(html):,} chars")
    print(f"  Real Idox data markers found: {real_data_hits if real_data_hits else 'NONE'}")
    print(f"  Results container found: {'YES' if has_results_container else 'NO'}")
    snippet = " ".join(html.split())[:2000]
    print(f"  Content snippet: {snippet!r}")

    path = f"/tmp/scraperapi_{slug(name)}_post_result.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Full HTML saved: {path}")

    return {
        "status": response.status_code,
        "real_data_hits": real_data_hits,
        "has_results_container": has_results_container,
    }


def main():
    print("SCRAPERAPI — round 5: the real two-step form submission.")
    print("Round 4 found the form's OWN action is monthlyListResults.do?action=")
    print("firstPage, submitted via POST with a real CSRF token — explaining why")
    print("round 3's plain GET to that same URL failed. This builds the genuine")
    print("POST request instead of interactive clicking.\n")

    if not API_KEY:
        print("Set SCRAPERAPI_KEY as an environment variable before running this —")
        print("never hardcode real credentials into this file.")
        sys.exit(1)

    results = {}
    for name, base_url, form_path, firstpage_path in TARGETS:
        results[name] = submit_monthly_list_form(name, base_url, form_path)

    print(f"\n\n{'=' * 70}")
    print("REAL SUMMARY — judge for yourself from the evidence above:")
    print("=" * 70)
    for name, result in results.items():
        if result.get("error"):
            print(f"  {name}: FAILED — {result['error']}")
        elif result.get("real_data_hits"):
            print(f"  {name}: REAL SUCCESS — genuine Idox data markers found")
        else:
            print(f"  {name}: loaded but no real-data markers — check the saved HTML directly")


if __name__ == "__main__":
    main()
