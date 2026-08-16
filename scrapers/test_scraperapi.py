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
     "https://upa.aberdeenshire.gov.uk/online-applications/search.do"
     "?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
    ("Babergh District Council",
     "https://planning.baberghmidsuffolk.gov.uk/online-applications/search.do"
     "?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
    ("Argyll and Bute Council",
     "https://publicaccess.argyll-bute.gov.uk/online-applications/search.do"
     "?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
]

RESULTS_CONTAINER_MARKERS = [
    'ul.searchresults', 'id="searchresults"', 'searchResultsContainer',
    'class="searchresults"',
]
WAF_BLOCK_MARKERS = [
    "429 too many requests", "too many requests", "unusual traffic",
    "access denied", "captcha", "cloudflare",
]
RESTRICTION_MARKERS = [
    "kyc", "not permitted", "restricted", "not available", "blocked domain",
]


def scrape_request(target_url: str, label: str, extra_params: dict = None) -> dict:
    """One real call to ScraperAPI — returns honest, direct diagnostics."""
    print(f"\n{'-' * 70}")
    print(f"{label}")
    print(f"Target URL: {target_url}")
    print("-" * 70)

    if not API_KEY:
        print("  ⚠ SCRAPERAPI_KEY not set — cannot test.")
        return {"error": "missing credentials"}

    params = {"api_key": API_KEY, "url": target_url, "premium": "true"}
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

    print(f"  Real content length: {len(html):,} chars")
    print(f"  Results container found: {'YES' if has_results_container else 'NO'}")
    print(f"  Restriction/policy markers found: {restriction_hits if restriction_hits else 'none'}")
    print(f"  WAF/block-page markers found: {waf_hits if waf_hits else 'none'}")

    snippet = " ".join(html.split())[:400]
    print(f"  Visible text snippet: {snippet!r}")

    return {
        "status": response.status_code,
        "length": len(html),
        "has_results_container": has_results_container,
        "waf_hits": waf_hits,
        "restriction_hits": restriction_hits,
    }


def main():
    print("SCRAPERAPI — real test across the same three confirmed-blocked councils")
    print("used for the Bright Data comparison, for a fair, direct read.\n")

    if not API_KEY:
        print("Set SCRAPERAPI_KEY as an environment variable before running this —")
        print("never hardcode real credentials into this file.")
        sys.exit(1)

    results = {}
    for name, url in TARGETS:
        results[name] = scrape_request(url, name)

    # Real, separate test of session persistence — directly relevant to
    # our session-dependent councils, using ScraperAPI's own documented
    # session_number mechanism rather than assuming it works
    print(f"\n\n{'=' * 70}")
    print("SESSION PERSISTENCE TEST — does session_number genuinely keep the")
    print("same IP/session across two separate calls?")
    print("=" * 70)
    aberdeenshire_name, aberdeenshire_url = TARGETS[0]
    homepage_url = "https://upa.aberdeenshire.gov.uk/online-applications/search.do?action=simple&searchType=Application"
    scrape_request(homepage_url, "Homepage visit, session_number=42", {"session_number": "42"})
    scrape_request(aberdeenshire_url, "Same session_number=42, immediately after", {"session_number": "42"})

    print(f"\n\n{'=' * 70}")
    print("REAL SUMMARY — judge for yourself from the evidence above:")
    print("=" * 70)
    restriction_count = 0
    for name, result in results.items():
        if result.get("error"):
            print(f"  {name}: FAILED — {result['error']}")
        elif result.get("empty_200"):
            print(f"  {name}: EMPTY 200 — genuinely blank body")
        elif result.get("restriction_hits"):
            print(f"  {name}: LIKELY HIT A RESTRICTION — {result['restriction_hits']}")
            restriction_count += 1
        elif result.get("has_results_container"):
            print(f"  {name}: REAL SUCCESS — results container found")
        elif result.get("waf_hits"):
            print(f"  {name}: STILL BLOCKED (WAF) — {result['waf_hits']}")
        else:
            print(f"  {name}: UNCLEAR — got 200 but no known marker matched, "
                  f"worth reading the snippet above directly")

    print(f"\n{restriction_count} of {len(TARGETS)} showed a likely restriction marker.")


if __name__ == "__main__":
    main()
