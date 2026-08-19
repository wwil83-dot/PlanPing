#!/usr/bin/env python3
"""
PlanFind — ScraperAPI test, Derby + North East Lincolnshire only
(2026-08-19).

DELIBERATELY MINIMAL, unlike the earlier scraperapi_test.py (round 7)
this reuses none of — that script solved a genuinely harder problem
(a CSRF-protected Idox form needing a real two-step session dance, for
3 different WAF-blocked councils). Derby and NE Lincolnshire don't have
that problem at all — real, direct evidence this session confirmed
both load completely normally for an ordinary residential/VPN
connection (no CAPTCHA, no CSRF dance, no security warning). The ONLY
confirmed variable is IP origin: both hang with zero network activity
from a DigitalOcean-hosted runner, specifically, but work identically
from a home ISP AND from Surfshark VPN in two different countries (UK
and France) — pointing at ASN-based datacenter blocking, not a
country-based or session-based block. So this test only needs to
answer one question: does ScraperAPI's proxy pool look "residential
enough" to get past that same check? Nothing more complex than that.

BUDGET, explicit and respected: real, current ScraperAPI pricing
(checked directly before writing this, not assumed) — a standard
request costs 1 credit; premium=true costs 10; ultra_premium=true
costs 30 AND ISN'T EVEN AVAILABLE on the free tier at all. The person
running this is on the free tier (1,000 credits total). This script
ONLY sends standard-tier requests (2 total, 1 per council) — no
premium/ultra_premium escalation happens automatically. If both fail,
the real, honest next question (worth deciding deliberately, not
auto-spent) is whether premium=true (10 credits each, 20 total) is
worth trying — that decision is intentionally left to a human, not
made by this script.

Also deliberately NOT testing Sheffield or Bassetlaw — their confirmed
real problem is a broken TLS certificate on their own server (NET::
ERR_CERT_AUTHORITY_INVALID under active HSTS enforcement, confirmed
directly via a real browser). A proxy changes where the request comes
FROM, not whether the target server's own certificate is valid —
spending credits on those two would tell us nothing new.
"""
import os
import sys
import requests

API_KEY = os.environ.get("SCRAPERAPI_KEY", "")
API_ENDPOINT = "https://api.scraperapi.com"

# Real, confirmed URLs — same ones used in priority1_diagnostic.py,
# which already confirmed these load fine for a real residential/VPN
# connection and only fail from the DigitalOcean-hosted runner.
TARGETS = [
    ("Derby City Council",
     "https://eplanning.derby.gov.uk/online-applications/search.do"
     "?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
    ("North East Lincolnshire Council",
     "https://planninganddevelopment.nelincs.gov.uk/online-applications/search.do"
     "?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
]

# Real, distinctive Idox monthly-list markers — confirms actual
# application data came back, not just a page that loaded.
REAL_DATA_MARKERS = ["application type", "date received", "ref. no"]
WAF_BLOCK_MARKERS = ["429 too many requests", "too many requests",
                      "unusual traffic", "access denied", "cloudflare"]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def test_standard_tier(name: str, url: str) -> dict:
    print(f"\n{'-' * 70}")
    print(f"{name} — standard tier (1 credit if successful)")
    print(f"URL: {url}")
    print("-" * 70)

    # Deliberately NO premium/ultra_premium params — cheapest possible
    # real test, matching the budget discipline in the module docstring.
    params = {"api_key": API_KEY, "url": url}

    try:
        response = requests.get(API_ENDPOINT, params=params, timeout=70)
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ Request itself failed: {e}")
        return {"name": name, "error": str(e)}

    print(f"  HTTP status from ScraperAPI: {response.status_code}")

    if response.status_code != 200:
        print(f"  Response body (first 500 chars): {response.text[:500]!r}")
        return {"name": name, "status": response.status_code,
                "error": f"non-200: {response.status_code}"}

    html = response.text
    html_lower = html.lower()
    real_data_hits = [m for m in REAL_DATA_MARKERS if m in html_lower]
    waf_hits = [m for m in WAF_BLOCK_MARKERS if m in html_lower]

    print(f"  Real content length: {len(html):,} chars")
    title_start = html_lower.find("<title>")
    title_end = html_lower.find("</title>")
    title = html[title_start+7:title_end] if title_start >= 0 and title_end > title_start else "(no title found)"
    print(f"  Real page title: {title!r}")
    print(f"  Real Idox data markers found: {real_data_hits if real_data_hits else 'NONE'}")
    print(f"  WAF/block markers found: {waf_hits if waf_hits else 'none'}")

    out_path = f"/tmp/scraperapi_standard_{slug(name)}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Full HTML saved: {out_path}")

    return {
        "name": name,
        "status": response.status_code,
        "length": len(html),
        "real_data_hits": real_data_hits,
        "waf_hits": waf_hits,
        "success": bool(real_data_hits),
    }


def main():
    print("SCRAPERAPI TEST — Derby + North East Lincolnshire, standard tier only")
    print("Budget: 2 requests max, 1 credit each if successful (free-tier-safe,")
    print("no premium/ultra_premium — ultra_premium isn't even available on")
    print("the free tier). Sheffield/Bassetlaw deliberately NOT tested — their")
    print("confirmed problem is a broken certificate on their own server, not")
    print("something a proxy can fix.\n")

    if not API_KEY:
        print("ERROR: Set SCRAPERAPI_KEY as an environment variable first.")
        sys.exit(1)

    results = [test_standard_tier(name, url) for name, url in TARGETS]

    print(f"\n\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        if r.get("error"):
            print(f"  {r['name']}: FAILED — {r['error']}")
        elif r.get("success"):
            print(f"  {r['name']}: REAL SUCCESS — genuine Idox data came through "
                  f"on the standard tier alone")
        else:
            print(f"  {r['name']}: loaded but no real Idox data found — "
                  f"check the saved HTML directly")

    any_failed = any(not r.get("success") for r in results)
    if any_failed:
        print("\nAt least one council did not succeed on the standard tier.")
        print("Before spending anything more: check the saved HTML for what")
        print("actually came back (a real WAF page? empty? something else?).")
        print("If it's worth it, the next real step would be premium=true")
        print("(10 credits per request, 20 total for both) — a deliberate")
        print("decision to make with real evidence in hand, not automatic.")
    else:
        print("\nBoth succeeded on the standard tier alone — no need to spend")
        print("anything more. This confirms ScraperAPI's base proxy pool is")
        print("enough for these two specifically.")


if __name__ == "__main__":
    main()
