"""Kensington & Chelsea recon, round 8 (council id 45). Writes nothing.

Round 7 showed how the Atlas search app builds its data calls:
    Fe = `${yk(userSearchItem)}` + (`?` + makeUrlParameters(filters) + `&sort=${sortId}`)
    cases        : `${Ff}/cases/${Fe}`          (response JSON has .results)
    mapPoints    : `${Ff}/searchMapPoints/${Fe}`
    counts       : `${Ff}/caseCounts/${Fe}`     (response has Applications / Decisions / Appeals)
    filters sent to the page URL: caseType, caseStatus, planningType, dateFrom/dateTo (epoch ms), dateWeeks, dateMonths
    (epoch ms comma lists), keywords = btoa(list.join($g)); sentinels Tg / jy / zy mean 'any'.
Still unknown: the value of Ff (API base), what yk(item) returns for an all-borough date search, what
tn.makeUrlParameters emits, and the numeric ids behind caseType / caseStatus / planningType / sort.

This round (one bundle download, 10 credits) prints those definitions.
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


def ident(name):
    return r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])"


def show_defs(js, name, limit=3, before=60, after=500):
    ms = list(re.finditer(ident(name) + r"\s*=\s*(?!=)", js))
    print(f"  '{name}' assignments: {len(ms)}")
    for m in ms[:limit]:
        print(f"    ...{span(js, m.start(), before, after)}...")
    if not ms:
        ms2 = list(re.finditer(r"function\s+" + re.escape(name) + r"\s*\(", js))
        for m in ms2[:limit]:
            print(f"    function: ...{span(js, m.start(), 20, after)}...")
        if not ms2:
            ms3 = list(re.finditer(ident(name), js))
            print(f"    no assignment; {len(ms3)} bare occurrences, first contexts:")
            for m in ms3[:3]:
                print(f"      ...{span(js, m.start(), 100, 260)}...")


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

    print("\n== A. API base Ff and site base Zu")
    for n in ("Ff", "Zu", "Tu"):
        show_defs(js, n, limit=3, before=40, after=300)
    print("  other mentions of 'Ff' (first 6):")
    for m in list(re.finditer(ident("Ff"), js))[:6]:
        print(f"    ...{span(js, m.start(), 90, 200)}...")

    print("\n== B. search item -> path (yk) and URL params (makeUrlParameters / makeCacheKey)")
    show_defs(js, "yk", limit=2, before=20, after=900)
    for key in ("makeUrlParameters", "makeCacheKey"):
        ms = list(re.finditer(re.escape(key) + r"\s*[:=(]", js))
        print(f"  '{key}' definitions/uses: {len(ms)}")
        for m in ms[:2]:
            print(f"    ...{span(js, m.start(), 80, 1100)}...")

    print("\n== C. sentinels and separators: $g (keyword join), Tg, jy, zy")
    for n in ("$g", "Tg", "jy", "zy"):
        show_defs(js, n, limit=1, before=20, after=200)

    print("\n== D. enums: search item type (Ke), page type (Ae), sort ids")
    for n in ("Ke", "Ae"):
        ms = list(re.finditer(r"var\s+" + re.escape(n) + r"\s*=\s*\(", js)) or list(re.finditer(ident(n) + r"\s*=\s*\(", js))
        print(f"  '{n}': {len(ms)}")
        for m in ms[:1]:
            print(f"    ...{span(js, m.start(), 10, 700)}...")
    for key in ("currentSortId", "sortId", "SortType", "sortOptions"):
        ms = list(re.finditer(key, js))
        print(f"  '{key}': {len(ms)}")
        for m in ms[:1]:
            print(f"    ...{span(js, m.start(), 150, 450)}...")

    print("\n== E. case type / status / planning type options shown to the user")
    for pat in (r"Split Decision", r"Granted", r"Withdrawn"):
        ms = list(re.finditer(pat, js))
        print(f"  '{pat}': {len(ms)}")
        for m in ms[:2]:
            print(f"    ...{span(js, m.start(), 350, 450)}...")

    print("\n== F. the response decoders (tl / nl) and case field names")
    for n in ("tl", "nl"):
        show_defs(js, n, limit=1, before=10, after=160)
    for key in ("currentPlanningState", "latestDate", "caseReference:", "applicantName", "proposal", "description"):
        ms = list(re.finditer(re.escape(key), js))
        print(f"  '{key}': {len(ms)}")
        for m in ms[:1]:
            print(f"    ...{span(js, m.start(), 120, 260)}...")
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
