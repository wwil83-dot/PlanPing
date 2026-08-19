#!/usr/bin/env python3
"""
PlanFind — ScraperAPI test, Derby + North East Lincolnshire only
(2026-08-19).

UPDATED after a real first run: a standard-tier GET to the monthly-list
URL genuinely succeeded for both councils — real 200, real page title
"Monthly List", a real monthlyListForm present, no WAF signature at
all. That confirms ScraperAPI's cheapest tier gets past whatever blocks
the DigitalOcean-hosted runner specifically (real, separate evidence
this session: both councils load fine from a home ISP and from
Surfshark VPN in two different countries, but hang with zero network
activity from DigitalOcean — pointing at ASN-based datacenter blocking,
not country or session-based blocking).

What that first run's own marker-check got wrong: it only looked for
POPULATED-RESULTS text (column headers like "date received"), which
can never appear on the initial form page — a real, standard Idox
monthly-list flow always serves the search FORM first, and needs an
actual form submission to reach real results. Real form structure
extracted directly from both councils' actual successful responses
(not guessed): a CSRF token, a dateType radio (DC_Validated/
DC_Decided), a month select, POSTed to
monthlyListResults.do?action=firstPage — the exact same URL path
already known to idox_scraper.py's own TRY_FIRSTPAGE_FALLBACK_COUNCILS
mechanism.

This version does the real two-step flow: fetch the form (fresh CSRF
token, since a token from an old saved file would be stale), then POST
it back with the real session cookies explicitly forwarded — applying
the hard-won lesson from the earlier scraperapi_test.py (round 7),
which found session_number alone wasn't reliably enough to carry
session state across two separate API calls.

BUDGET, explicit and respected: real, current ScraperAPI pricing
(checked directly, not assumed) — a standard request costs 1 credit;
premium=true costs 10; ultra_premium=true costs 30 AND ISN'T EVEN
AVAILABLE on the free tier at all. The person running this is on the
free tier (1,000 credits total). UPDATED after a real second run: the
standard-tier POST (Step 2) failed with ScraperAPI's own explicit
error — "Protected domains may require adding premium=true OR
ultra_premium=true" — and confirmed NOT charged for that failure
(stated directly in the same error response). Given that specific,
real instruction from ScraperAPI itself, Step 2 now uses premium=true
— explicitly approved by the person running this before making the
change, not a silent escalation. Step 1 stays on the free standard
tier, since it already works there. Worst case this run: 22 credits
(2 x 1 for Step 1, 2 x 10 for Step 2) — failed requests still aren't
charged either way.

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

# REAL field structure, extracted directly from both councils' actual
# successful standard-tier responses (2026-08-19) — not guessed. Both
# councils share the identical standard Idox monthly-list form shape:
# CSRF token + dateType radio (DC_Validated/DC_Decided) + month select
# + searchType, POSTed to monthlyListResults.do?action=firstPage. This
# exact URL path is already a known quantity in idox_scraper.py's own
# TRY_FIRSTPAGE_FALLBACK_COUNCILS mechanism — nothing exotic here.


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def get_real_form(name: str, url: str) -> dict:
    """Step 1 — fetch the real form page and extract its real, live
    CSRF token + real cookies for this specific session. A FRESH
    request each time this runs — a CSRF token from an old saved file
    would already be stale/expired."""
    print(f"\n{'-' * 70}")
    print(f"{name} — Step 1: fetch real form (1 credit)")
    print("-" * 70)

    params = {"api_key": API_KEY, "url": url}
    try:
        response = requests.get(API_ENDPOINT, params=params, timeout=70)
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ Request failed: {e}")
        return {"error": str(e)}

    print(f"  HTTP status: {response.status_code}")
    if response.status_code != 200:
        return {"error": f"non-200: {response.status_code}"}

    real_cookies = dict(response.cookies)
    print(f"  Real cookies received: {real_cookies if real_cookies else 'NONE'}")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, "html.parser")
    form = soup.find("form", id="monthlyListForm") or soup.find("form")
    if not form:
        print(f"  ⚠ No form found in this response.")
        return {"error": "no form found"}

    csrf_token = None
    for el in form.find_all("input"):
        if el.get("name") == "_csrf":
            csrf_token = el.get("value")
            break

    if not csrf_token:
        print(f"  ⚠ No _csrf token found.")
        return {"error": "no csrf token"}

    print(f"  Real, fresh CSRF token: {csrf_token}")
    action = form.get("action", "")
    from urllib.parse import urlparse
    parsed = urlparse(url)
    post_target = f"{parsed.scheme}://{parsed.netloc}{action}" if action.startswith("/") else action

    return {
        "csrf": csrf_token,
        "cookies": real_cookies,
        "post_target": post_target,
    }


def submit_real_form(name: str, step1: dict) -> dict:
    """Step 2 — POST the real form fields back, explicitly forwarding
    the real cookies from Step 1 (the lesson learned the hard way in
    the earlier round-7 script: don't just trust session_number alone
    to carry session state invisibly)."""
    print(f"\n{'-' * 70}")
    print(f"{name} — Step 2: submit real form (premium=true, 10 credits)")
    print(f"POST target: {step1['post_target']}")
    print("-" * 70)

    post_data = {
        "_csrf": step1["csrf"],
        "month": "0",
        "dateType": "DC_Validated",
        "searchType": "Application",
    }
    print(f"  POST body: {post_data}")

    cookie_header = "; ".join(f"{k}={v}" for k, v in step1["cookies"].items())
    headers = {"Cookie": cookie_header} if cookie_header else {}
    # CHANGED 2026-08-19 — real evidence: the standard-tier POST failed
    # with a real, explicit ScraperAPI error message: "Protected domains
    # may require adding premium=true OR ultra_premium=true parameter to
    # your request." Not charged for that failure (per ScraperAPI's own
    # stated policy, confirmed in the same error body). Escalating ONLY
    # this step to premium=true (10 credits) — Step 1 stays on the free
    # standard tier since it already works there, no reason to pay more
    # for something that isn't the problem.
    params = {"api_key": API_KEY, "url": step1["post_target"],
              "keep_headers": "true", "premium": "true"}

    try:
        response = requests.post(API_ENDPOINT, params=params, data=post_data,
                                  headers=headers, timeout=70)
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ Request failed: {e}")
        return {"error": str(e)}

    print(f"  HTTP status: {response.status_code}")
    if response.status_code != 200:
        print(f"  Body (first 500 chars): {response.text[:500]!r}")
        return {"error": f"non-200: {response.status_code}"}

    html = response.text
    html_lower = html.lower()
    real_data_hits = [m for m in REAL_DATA_MARKERS if m in html_lower]
    waf_hits = [m for m in WAF_BLOCK_MARKERS if m in html_lower]
    has_results = "searchresults" in html_lower

    print(f"  Real content length: {len(html):,} chars")
    print(f"  Real Idox data markers found: {real_data_hits if real_data_hits else 'NONE'}")
    print(f"  'searchresults' container present: {has_results}")
    print(f"  WAF markers found: {waf_hits if waf_hits else 'none'}")

    out_path = f"/tmp/scraperapi_step2_{slug(name)}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Full HTML saved: {out_path}")

    return {
        "status": response.status_code,
        "real_data_hits": real_data_hits,
        "has_results": has_results,
        "success": bool(real_data_hits) or has_results,
    }


def main():
    print("SCRAPERAPI TEST — Derby + North East Lincolnshire, real two-step flow")
    print("Budget: Step 1 (fetch form) on standard tier, 1 credit each if")
    print("successful — already confirmed working. Step 2 (submit form) now on")
    print("premium=true, 10 credits each if successful, per ScraperAPI's own")
    print("explicit error message on the standard tier: 'Protected domains may")
    print("require adding premium=true'. Worst case: 22 credits total (2x1 +")
    print("2x10) — failed requests aren't charged, per ScraperAPI's own stated")
    print("policy, confirmed directly in the previous run's error response.\n")

    if not API_KEY:
        print("ERROR: Set SCRAPERAPI_KEY as an environment variable first.")
        sys.exit(1)

    results = []
    for name, url in TARGETS:
        step1 = get_real_form(name, url)
        if step1.get("error"):
            results.append({"name": name, "error": f"Step 1 failed: {step1['error']}"})
            continue
        step2 = submit_real_form(name, step1)
        step2["name"] = name
        results.append(step2)

    print(f"\n\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        if r.get("error"):
            print(f"  {r['name']}: FAILED — {r['error']}")
        elif r.get("success"):
            print(f"  {r['name']}: REAL SUCCESS — genuine results came through "
                  f"the full two-step flow on the standard tier")
        else:
            print(f"  {r['name']}: form submitted but no real results found — "
                  f"check the saved HTML directly")


if __name__ == "__main__":
    main()
