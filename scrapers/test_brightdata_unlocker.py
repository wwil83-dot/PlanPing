#!/usr/bin/env python3
"""
PlanFind — Bright Data Web Unlocker API test (2026-08-16).

PURPOSE: Real, direct test of whether Web Unlocker API can get past
whatever's blocking Aberdeenshire Council's Idox portal (confirmed
"Page load timeout" via idox_multi_recon.py, matching the same real
signature as the existing ~13-council WAF-blocked group).

Tests TWO real, separate things, since Web Unlocker's own docs say it's
built for single, stateless HTTP requests — not browser sessions:
  1. The monthly-list URL directly, cold — no prior session-establishing
     visit at all. If this alone works, our production flow's "visit
     homepage first" step may not even be necessary for this API.
  2. The SAME URL, but preceded by a real homepage visit through Web
     Unlocker too — testing whether any session/cookie state genuinely
     persists across two separate API calls, or whether each call is
     fully independent (in which case a session-dependent council would
     never work through this API at all, regardless of how it's called).

Credentials read from environment variables — never hardcoded, matching
the same convention already used for SUPABASE_URL/SUPABASE_KEY
throughout this whole project.
"""
import os
import sys
import requests

API_KEY = os.environ.get("BRIGHTDATA_API_KEY", "")
ZONE_NAME = os.environ.get("BRIGHTDATA_ZONE_NAME", "")
API_ENDPOINT = "https://api.brightdata.com/request"

# Aberdeenshire — confirmed real failure via idox_multi_recon.py
# (genuine "Page load timeout", same signature as the existing WAF group)
BASE_URL = "https://upa.aberdeenshire.gov.uk/online-applications"
MONTHLY_LIST_URL = (
    f"{BASE_URL}/search.do?action=monthlyList"
    f"&searchCriteria.monthYearIndex=0&searchType=Application"
)
HOMEPAGE_URL = f"{BASE_URL}/search.do?action=simple&searchType=Application"

RESULTS_CONTAINER_MARKERS = [
    'ul.searchresults', 'id="searchresults"', 'searchResultsContainer',
    'class="searchresults"',
]
WAF_BLOCK_MARKERS = [
    "429 too many requests", "too many requests", "unusual traffic",
    "access denied", "captcha", "cloudflare",
]


def unlock_request(target_url: str, label: str) -> dict:
    """One real call to Web Unlocker API — returns the raw response text
    plus a few honest, direct diagnostics, no guessing."""
    print(f"\n{'=' * 70}")
    print(f"{label}")
    print(f"Target URL: {target_url}")
    print("=" * 70)

    if not API_KEY or not ZONE_NAME:
        print("  ⚠ BRIGHTDATA_API_KEY or BRIGHTDATA_ZONE_NAME not set — cannot test.")
        return {"error": "missing credentials"}

    try:
        response = requests.post(
            API_ENDPOINT,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={"zone": ZONE_NAME, "url": target_url, "format": "raw"},
            timeout=60,
        )
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ Request itself failed: {e}")
        return {"error": str(e)}

    print(f"  HTTP status from Bright Data: {response.status_code}")

    if response.status_code != 200:
        print(f"  Response body (first 500 chars): {response.text[:500]!r}")
        return {"error": f"non-200: {response.status_code}", "body": response.text[:2000]}

    html = response.text
    html_lower = html.lower()

    has_results_container = any(m.lower() in html_lower for m in RESULTS_CONTAINER_MARKERS)
    waf_hits = [m for m in WAF_BLOCK_MARKERS if m in html_lower]

    print(f"  Real content length: {len(html):,} chars")
    print(f"  Results container found: {'YES' if has_results_container else 'NO'}")
    print(f"  WAF/block-page markers found: {waf_hits if waf_hits else 'none'}")

    # Real, honest snippet for direct human judgment — same principle as
    # applicant_agent_recon.py's raw-context approach, not a guessed
    # summary
    snippet = " ".join(html.split())[:400]
    print(f"  Visible text snippet: {snippet!r}")

    return {
        "status": response.status_code,
        "length": len(html),
        "has_results_container": has_results_container,
        "waf_hits": waf_hits,
        "html": html,
    }


def main():
    print("BRIGHT DATA WEB UNLOCKER API — real test against Aberdeenshire")

    if not API_KEY or not ZONE_NAME:
        print("\nSet BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE_NAME as environment")
        print("variables before running this — never hardcode real credentials")
        print("into this file.")
        sys.exit(1)

    # TEST 1 — cold, direct request, no prior session at all
    result1 = unlock_request(
        MONTHLY_LIST_URL,
        "TEST 1: Monthly list URL, cold (no prior session-establishing visit)",
    )

    # TEST 2 — homepage first, then the same monthly list URL, to check
    # whether session state genuinely carries between two SEPARATE API
    # calls, or whether each call is fully independent
    print("\n\nNow testing whether session state persists across two separate calls...")
    unlock_request(HOMEPAGE_URL, "TEST 2a: Homepage visit first (establishing a session, if any)")
    result2b = unlock_request(
        MONTHLY_LIST_URL,
        "TEST 2b: Same monthly list URL, immediately after the homepage visit above",
    )

    print(f"\n\n{'=' * 70}")
    print("REAL SUMMARY — judge for yourself from the evidence above:")
    print("=" * 70)
    for label, result in [("Test 1 (cold)", result1), ("Test 2b (after homepage visit)", result2b)]:
        if result.get("error"):
            print(f"  {label}: FAILED — {result['error']}")
        elif result.get("has_results_container"):
            print(f"  {label}: REAL SUCCESS — results container found in the response")
        elif result.get("waf_hits"):
            print(f"  {label}: STILL BLOCKED — {result['waf_hits']}")
        else:
            print(f"  {label}: UNCLEAR — got a 200 response but no results container "
                  f"or known block marker matched; worth reading the snippet above directly")


if __name__ == "__main__":
    main()
