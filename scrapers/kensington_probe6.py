"""Kensington & Chelsea recon, round 6 (council id 45). Writes nothing.

Round 5: https://www.rbkc.gov.uk/planningsearch is a SolidStart single-page app (the 'Atlas' portal, plausible domain
atlas.rbkc.gov.uk). Its filters: free text (address/postcode), application type (Application / Decision / Appeal),
status (Granted / Refused / Split Decision / Withdrawn), period (week / month / from-to dd/mm/yyyy). The bundle
/planningsearch/_build/assets/client-*.js contains `${Zu}/api/view-pdf/`, so the data comes from an API we have not seen.

This round downloads the main bundle plus the route chunks listed in window.manifest (via ScraperAPI plain tier, about
10-20 requests) and prints:
  - where the API base is defined (the variable behind `/api/view-pdf/`)
  - every '/api/...' path string with context
  - every fetch(...) / ofetch / $fetch / trpc / server-function style call with context
  - any rbkc / atlas / tytl hostnames
"""
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

KEY = os.environ.get("SCRAPERAPI_KEY", "")
BASE = "https://www.rbkc.gov.uk"
PAGE = f"{BASE}/planningsearch"
API = "https://api.scraperapi.com/"


def fetch(url, quiet=False):
    params = {"api_key": KEY, "url": url, "country_code": "gb"}
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, timeout=150, follow_redirects=True)
    except Exception as e:
        print(f"  ERROR {url}: {str(e)[:150]}")
        return None
    if not quiet:
        print(f"  GET {url} -> HTTP {r.status_code} {len(r.text)}b ({time.time() - t0:.0f}s)")
    return r


def ctx(js, m, before=140, after=240):
    return js[max(0, m.start() - before): m.end() + after].replace("\n", " ")


def analyse(name, js, seen_api):
    print(f"\n  ### {name}  ({len(js)} chars)")
    # 1. host names
    hosts = sorted(set(re.findall(r"https?://[A-Za-z0-9.\-]*(?:rbkc|atlas|tytl)[A-Za-z0-9.\-/_]*", js)))
    for h in hosts[:20]:
        print(f"    host: {h}")
    # 2. /api/ strings
    n = 0
    for m in re.finditer(r"""(["'`])((?:[^"'`\\]|\\.)*?/api/(?:[^"'`\\]|\\.)*?)\1""", js):
        s = m.group(2)
        if s in seen_api:
            continue
        seen_api.add(s)
        print(f"    api: {s[:200]}")
        n += 1
        if n >= 60:
            break
    # 3. template-literal uses of /api/ with a variable base
    for m in re.finditer(r"\$\{[A-Za-z_$][\w$]*\}/api/[A-Za-z0-9_\-/${}.]*", js):
        s = m.group(0)
        if s in seen_api:
            continue
        seen_api.add(s)
        print(f"    api-tpl: {s[:200]}")
        print(f"        ctx: ...{ctx(js, m)}...")
    # 4. definition of the base variable(s)
    for var in set(re.findall(r"\$\{([A-Za-z_$][\w$]*)\}/api/", js)):
        for m in list(re.finditer(r"(?:const|let|var|,)\s*" + re.escape(var) + r"\s*=\s*[^;,]{1,200}", js))[:3]:
            print(f"    base var {var}: ...{ctx(js, m, 20, 220)}...")
    # 5. fetch / rpc style calls
    k = 0
    for m in re.finditer(r"(fetch\(|\$fetch|ofetch|axios\.|\.post\(|\.get\(|createServerFn|\"use server\"|trpc|graphql|\bquery\s*\()", js):
        c = ctx(js, m, 60, 260)
        if re.search(r"/api/|search|application|planning|case|decision|graphql|trpc", c, re.I):
            print(f"    call: ...{c}...")
            k += 1
            if k >= 25:
                break
    # 6. search/application field names that hint at the payload
    for pat in [r"(applicationType|application_type|decisionDate|decision_date|receivedDate|received_date|dateFrom|date_from|startDate|endDate|caseRef|reference\b)[^,;]{0,80}"]:
        found = sorted(set(m.group(0)[:100] for m in re.finditer(pat, js)))[:25]
        for f in found:
            print(f"    field: {f}")


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1
    r = fetch(PAGE)
    if r is None or r.status_code != 200:
        return 1
    soup = BeautifulSoup(r.text, "html.parser")
    files = []
    for s in soup.find_all("script", src=True):
        if "_build/assets" in s["src"]:
            files.append(urljoin(PAGE, s["src"]))
    # window.manifest -> route chunks
    for s in soup.find_all("script"):
        body = s.string or s.get_text() or ""
        if "window.manifest" in body:
            outs = re.findall(r'"output":"(/planningsearch/_build/assets/[^"]+\.js)"', body)
            print(f"  manifest lists {len(outs)} route chunks:")
            for o in outs:
                print(f"      {o}")
                files.append(BASE + o)
    # de-dup and also fetch the modulepreload'd JS (not echarts/mapbox/jspdf/turf/heic/compressor vendors)
    for l in soup.find_all("link", href=True):
        h = l["href"]
        if h.endswith(".js") and "_build/assets" in h and not re.search(r"echarts|mapbox|jspdf|turf|heic|compressor|seroval", h):
            files.append(urljoin(PAGE, h))
    uniq = []
    for f in files:
        if f not in uniq:
            uniq.append(f)
    print(f"\n  {len(uniq)} bundle files to scan")
    seen_api = set()
    for f in uniq[:18]:
        j = fetch(f, quiet=True)
        if j is None or j.status_code != 200:
            print(f"  {f}: {j.status_code if j is not None else 'error'}")
            continue
        analyse(f.rsplit("/", 1)[-1], j.text, seen_api)
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
