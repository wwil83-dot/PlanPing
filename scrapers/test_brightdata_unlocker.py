#!/usr/bin/env python3
"""
PlanFind — Bright Data Web Unlocker API test, round 2 (2026-08-16).

PURPOSE: Round 1 (Aberdeenshire only) hit Bright Data's own "no-KYC
residential access mode" policy gate — a real, direct finding, but one
that says nothing about whether Aberdeenshire's WAF itself is beatable.
A real, honest question was raised: does this KYC wall apply to ALL our
target councils, or was that just an assumption based on how the
no-KYC allowlist works, never actually tested against a second council?
This round tests several real, confirmed-blocked councils at once —
including Babergh specifically, which showed a genuinely DIFFERENT
failure mode tonight (a real 429, not a timeout like Aberdeenshire),
making it a useful, distinct second data point either way.

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

# (name, monthly-list URL) — a real spread of confirmed-blocked councils
# from tonight's own batch-4 log, deliberately including different
# failure signatures (Aberdeenshire = timeout, Babergh/Argyll and Bute
# = explicit 429) rather than just retesting the same one.
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
KYC_MARKERS = ["kyc", "no-kyc", "not available for immediate residential"]


def unlock_request(target_url: str, label: str) -> dict:
    """One real call to Web Unlocker API — returns the raw response text
    plus a few honest, direct diagnostics, no guessing."""
    print(f"\n{'-' * 70}")
    print(f"{label}")
    print(f"Target URL: {target_url}")
    print("-" * 70)

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

    # ADDED (2026-08-16) — a real gap in round 2's diagnostics. Babergh
    # and Argyll and Bute both came back 200 with a genuinely empty
    # body — not a recognized block marker, not the KYC wall, just
    # nothing. Bright Data's own docs mention response headers can
    # carry real diagnostic info (e.g. an unblock-expect-style header)
    # that the body alone doesn't show. Printing every header now,
    # rather than only ever looking at the body.
    print(f"  Response headers from Bright Data:")
    for k, v in response.headers.items():
        print(f"    {k}: {v}")

    if response.status_code != 200:
        print(f"  Response body (first 500 chars): {response.text[:500]!r}")
        return {"error": f"non-200: {response.status_code}", "body": response.text[:2000]}

    html = response.text
    html_lower = html.lower()

    if len(html) == 0:
        print(f"  ⚠ Real content length: 0 chars — genuinely empty body despite HTTP 200.")
        print(f"  This is NOT the same as the KYC wall (that returns real explanatory text)")
        print(f"  and NOT a recognized WAF block page — worth treating as its own, distinct")
        print(f"  outcome rather than assuming it means the same thing as either.")
        return {"status": 200, "length": 0, "empty_200": True}

    has_results_container = any(m.lower() in html_lower for m in RESULTS_CONTAINER_MARKERS)
    waf_hits = [m for m in WAF_BLOCK_MARKERS if m in html_lower]
    kyc_hits = [m for m in KYC_MARKERS if m in html_lower]

    print(f"  Real content length: {len(html):,} chars")
    print(f"  Results container found: {'YES' if has_results_container else 'NO'}")
    print(f"  KYC-wall markers found: {kyc_hits if kyc_hits else 'none'}")
    print(f"  WAF/block-page markers found: {waf_hits if waf_hits else 'none'}")

    snippet = " ".join(html.split())[:400]
    print(f"  Visible text snippet: {snippet!r}")

    return {
        "status": response.status_code,
        "length": len(html),
        "has_results_container": has_results_container,
        "waf_hits": waf_hits,
        "kyc_hits": kyc_hits,
    }


def main():
    print("BRIGHT DATA WEB UNLOCKER API — round 2: testing across multiple councils")
    print("Real question: does the KYC wall from round 1 apply broadly, or was")
    print("that an untested assumption based on Aberdeenshire alone?\n")

    if not API_KEY or not ZONE_NAME:
        print("Set BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE_NAME as environment")
        print("variables before running this — never hardcode real credentials")
        print("into this file.")
        sys.exit(1)

    results = {}
    for name, url in TARGETS:
        results[name] = unlock_request(url, name)

    print(f"\n\n{'=' * 70}")
    print("REAL SUMMARY — judge for yourself from the evidence above:")
    print("=" * 70)
    kyc_count = 0
    empty_count = 0
    for name, result in results.items():
        if result.get("error"):
            print(f"  {name}: FAILED — {result['error']}")
        elif result.get("empty_200"):
            print(f"  {name}: EMPTY 200 — genuinely blank body, distinct from both "
                  f"the KYC wall and a recognized WAF block; see headers above")
            empty_count += 1
        elif result.get("kyc_hits"):
            print(f"  {name}: HIT THE SAME KYC WALL")
            kyc_count += 1
        elif result.get("has_results_container"):
            print(f"  {name}: REAL SUCCESS — results container found")
        elif result.get("waf_hits"):
            print(f"  {name}: STILL BLOCKED (WAF) — {result['waf_hits']}")
        else:
            print(f"  {name}: UNCLEAR — got 200 but no known marker matched, "
                  f"worth reading the snippet above directly")

    print(f"\n{kyc_count} of {len(TARGETS)} hit the KYC wall, "
          f"{empty_count} of {len(TARGETS)} came back genuinely empty.")
    if kyc_count == len(TARGETS):
        print("Confirms the KYC wall is a real, general policy — not Aberdeenshire-specific.")
    elif kyc_count == 0 and empty_count == 0:
        print("The KYC wall was NOT hit again — worth re-examining what made "
              "Aberdeenshire's request different.")
    else:
        print("Mixed result — three different councils, three different outcomes. "
              "Worth treating each as its own case rather than one general answer.")


if __name__ == "__main__":
    main()
