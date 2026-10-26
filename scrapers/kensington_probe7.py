"""Kensington & Chelsea recon, round 7 (council id 45). Writes nothing.

Round 6 found in client-Dj7XTNb0.js:
    `${Ff}/searchMapPoints/${Fe}`   `${Ff}/caseCounts/${Fe}`   fetch(Ie), fetch(Ee)  (two more URLs)
    case objects with caseReferenceId, casePlanningStateId, currentStageType, currentStageStatusName, latitude,
    longitude, localUprnPointId, cases[]; search URL params dateFrom/dateTo/dateWeeks/dateMonths/keywords/caseType/
    caseStatus/planningType/page/sort/limit; a case detail loaded from `${...[caseReference].fileJsonPath}`.
    Other hosts: atlas.rbkc.gov.uk, planningsearch.rbkc.gov.uk/publisher/mvc/listDocuments.

This round (one bundle download, 10 credits) prints the code that BUILDS those URLs:
  - the definitions of the base variable(s) (Ff etc.) and of Fe / Ie / Ee
  - generous context around searchMapPoints, caseCounts, searchCaseTypeCountsUrl, fileJsonPath, caseReferenceId
  - the function that turns the filter object into a query string / path
  - the case-detail loader and any 'cases' endpoint
"""
import os
import re
import sys
import time

import httpx

KEY = os.environ.get("SCRAPERAPI_KEY", "")
URL = "https://www.rbkc.gov.uk/planningsearch/_build/assets/client-Dj7XTNb0.js"
API = "https://api.scraperapi.com/"


def span(js, i, before, after):
    return js[max(0, i - before): i + after].replace("\n", " ")


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1
    t0 = time.time()
    r = httpx.get(API, params={"api_key": KEY, "url": URL, "country_code": "gb"}, timeout=150, follow_redirects=True)
    js = r.text
    print(f"GET bundle -> HTTP {r.status_code} {len(js)} chars ({time.time() - t0:.0f}s)")
    if r.status_code != 200:
        return 1
    open("/tmp/kc_client.js", "w", encoding="utf-8").write(js)

    print("\n== A. base variables for the data endpoints")
    bases = set()
    for key in ("searchMapPoints", "caseCounts"):
        for m in re.finditer(r"\$\{([A-Za-z_$][\w$]*)\}/" + key, js):
            bases.add(m.group(1))
    print(f"  variables used before /searchMapPoints|/caseCounts: {sorted(bases)}")
    for v in sorted(bases):
        n = 0
        for m in re.finditer(r"(?<![\w$.])" + re.escape(v) + r"\s*=\s*(?!=)", js):
            print(f"  def {v}: ...{span(js, m.start(), 80, 420)}...")
            n += 1
            if n >= 3:
                break

    print("\n== B. searchMapPoints / caseCounts: surrounding code")
    for key in ("searchMapPoints", "caseCounts"):
        ms = list(re.finditer(key, js))
        print(f"  '{key}': {len(ms)} occurrence(s)")
        for m in ms[:2]:
            print(f"  --- {key} @ {m.start()}")
            print("  " + span(js, m.start(), 1100, 1700))

    print("\n== C. other URL strings built from template literals with a base variable")
    seen = set()
    for m in re.finditer(r"`\$\{([A-Za-z_$][\w$]*)\}/([A-Za-z0-9_\-/]+)[^`]{0,120}`", js):
        s = m.group(0)
        if s in seen:
            continue
        seen.add(s)
        print(f"  {s[:220]}")
        if len(seen) > 60:
            break

    print("\n== D. fileJsonPath / case detail loading")
    for key in ("fileJsonPath", "caseReferenceId=", "searchCasesUrl", "searchCaseTypeCountsUrl", "casesQuery", "getCases"):
        ms = list(re.finditer(re.escape(key), js))
        print(f"  '{key}': {len(ms)} occurrence(s)")
        for m in ms[:1]:
            print("  " + span(js, m.start(), 500, 900))

    print("\n== E. query-string builder (filters -> URL)")
    for m in list(re.finditer(r"new URLSearchParams\(", js))[:6]:
        c = span(js, m.start(), 200, 500)
        if re.search(r"dateFrom|keywords|caseType|caseStatus|planningType", c):
            print("  " + c)
            print("  ---")
    for key in ("dateFrom", "caseStatus"):
        for m in list(re.finditer(key, js))[:6]:
            c = span(js, m.start(), 150, 350)
            if re.search(r"fetch|URL|url|query|path|encodeURI", c):
                print(f"  [{key}] {c}")
                print("  ---")

    print("\n== F. enums that map ids to labels (planning state / status / case type)")
    for key in ("casePlanningStateId", "currentStageStatusName", "caseStatus", "Z7="):
        ms = list(re.finditer(re.escape(key), js))
        print(f"  '{key}': {len(ms)} occurrence(s)")
    for m in list(re.finditer(r"Z7\s*=", js))[:2]:
        print("  Z7 def: " + span(js, m.start(), 20, 700))

    print("\nDone. (bundle saved to /tmp/kc_client.js on the runner only)")


if __name__ == "__main__":
    sys.exit(main())
