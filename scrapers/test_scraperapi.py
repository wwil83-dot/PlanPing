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
import requests

API_KEY = os.environ.get("SCRAPERAPI_KEY", "")
API_ENDPOINT = "https://api.scraperapi.com"

TARGETS = [
    ("Aberdeenshire Council",
     "https://upa.aberdeenshire.gov.uk/online-applications/monthlyListResults.do?action=firstPage"),
    ("Babergh District Council",
     "https://planning.baberghmidsuffolk.gov.uk/online-applications/monthlyListResults.do?action=firstPage"),
    ("Argyll and Bute Council",
     "https://publicaccess.argyll-bute.gov.uk/online-applications/monthlyListResults.do?action=firstPage"),
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
    return name.lower().replace(" ", "_").replace(".", "")


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

    # ADDED (2026-08-16) — session_number now included by default, not
    # just in the separate session-persistence experiment. Round 1's
    # results strongly suggested this was the real difference between
    # failure and success for Aberdeenshire — worth testing that
    # directly against all three councils, not just one.
    params = {"api_key": API_KEY, "url": target_url, "premium": "true",
              "session_number": "101"}
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
    }


def main():
    print("SCRAPERAPI — round 2: retesting all three councils WITH session_number")
    print("included by default, since round 1 strongly suggested that was the real")
    print("difference between Aberdeenshire's failure and the session-test success.\n")

    if not API_KEY:
        print("Set SCRAPERAPI_KEY as an environment variable before running this —")
        print("never hardcode real credentials into this file.")
        sys.exit(1)

    results = {}
    for name, url in TARGETS:
        results[name] = scrape_request(url, name, save_html=True)

    print(f"\n\n{'=' * 70}")
    print("REAL SUMMARY — judge for yourself from the evidence above:")
    print("=" * 70)
    real_success_count = 0
    restriction_count = 0
    for name, result in results.items():
        if result.get("error"):
            print(f"  {name}: FAILED — {result['error']}")
        elif result.get("empty_200"):
            print(f"  {name}: EMPTY 200 — genuinely blank body")
        elif result.get("restriction_hits"):
            print(f"  {name}: LIKELY HIT A RESTRICTION — {result['restriction_hits']}")
            restriction_count += 1
        elif result.get("real_data_hits"):
            print(f"  {name}: REAL SUCCESS — genuine Idox data markers found "
                  f"({result['real_data_hits']}), not just a page that loaded")
            real_success_count += 1
        elif result.get("waf_hits"):
            print(f"  {name}: STILL BLOCKED (WAF) — {result['waf_hits']}")
        else:
            print(f"  {name}: LOADED but no real-data markers found — worth reading "
                  f"the full saved HTML directly before calling this a success")

    print(f"\n{real_success_count} of {len(TARGETS)} showed genuine real application data.")
    print(f"{restriction_count} of {len(TARGETS)} showed a likely restriction marker.")


if __name__ == "__main__":
    main()
