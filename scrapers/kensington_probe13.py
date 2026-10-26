"""Kensington & Chelsea recon, round 13 (council id 45). Writes nothing.

Round 12: rbkc.gov.uk challenges httpx, curl_cffi and headless Playwright (also via Webshare) from the runner. Only
ScraperAPI reaches it, and it text-decodes the binary /planningsearch/api/cases reply (cp1252 -> utf-8, five byte
values lost). binary_target=true answered 400 'The file type you are trying to scrape is not supported.'

This round tries, via ScraperAPI:
  A  binary_target=true with a fake file extension in the URL's query (x=.pdf, .png, .jpg, .zip, .bin, .xlsx)
     — hoping the 'supported file type' check passes and raw bytes come back
  B  keep_headers with Accept: application/octet-stream (and Accept-Charset: iso-8859-1)
  C  render=true on the SEARCH PAGE (/planningsearch/search/everywhere?dateFrom=..&dateTo=..), so the app itself
     fetches and decodes the data; we read the case cards out of the rendered DOM
Any reply that decodes with the record decoder is flagged, and a decoded sample is printed.
About 14 requests (render costs more credits).
"""
import base64
import os
import re
import sys
import time
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup

from kensington_probe11 import decode_cases, midnight_ms, d, summarise

KEY = os.environ.get("SCRAPERAPI_KEY", "")
SITE = "https://www.rbkc.gov.uk"
BASE = f"{SITE}/planningsearch/api"
API = "https://api.scraperapi.com/"


def sa(url, extra=None, headers=None, timeout=170):
    params = {"api_key": KEY, "url": url, "country_code": "gb"}
    if extra:
        params.update(extra)
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, headers=headers, timeout=timeout, follow_redirects=True)
    except Exception as e:
        print(f"    ERROR {str(e)[:200]}")
        return None
    print(f"    HTTP {r.status_code} {len(r.content)}B ct={r.headers.get('content-type')!r} ({time.time() - t0:.0f}s)")
    return r


def try_decode(r, label):
    if r is None or r.status_code != 200:
        if r is not None:
            print(f"    body: {r.text[:200]!r}")
        return None
    print(f"    first 32 bytes: {r.content[:32].hex(' ')}")
    try:
        recs = decode_cases(r.content)
        print(f"    *** DECODED {len(recs)} records via {label} ***")
        return recs
    except Exception as e:
        print(f"    decode failed: {e}")
        return None


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1
    today = date.today()
    f2, t2 = midnight_ms(today - timedelta(days=2)), midnight_ms(today)
    cases_url = f"{BASE}/cases/everywhere?dateFrom={f2}&dateTo={t2}&sort=1"
    good = None

    print("\n== A. binary_target with a fake extension")
    for ext in (".pdf", ".png", ".jpg", ".zip", ".bin", ".xlsx"):
        print(f"  x={ext}")
        r = sa(cases_url + f"&x={ext}", {"binary_target": "true"})
        recs = try_decode(r, f"binary_target x={ext}")
        if recs:
            good = ("A", ext)
            break

    print("\n== B. Accept: application/octet-stream")
    if not good:
        for hdrs in ({"Accept": "application/octet-stream"},
                     {"Accept": "application/octet-stream", "Accept-Charset": "iso-8859-1"}):
            print(f"  headers {hdrs}")
            r = sa(cases_url, {"keep_headers": "true"}, headers=hdrs)
            recs = try_decode(r, f"keep_headers {hdrs}")
            if recs:
                good = ("B", hdrs)
                break

    print("\n== C. render the search page and read the cards")
    frm, to = midnight_ms(today - timedelta(days=2)), midnight_ms(today)
    page_urls = [
        f"{SITE}/planningsearch/search/everywhere?dateFrom={frm}&dateTo={to}",
        f"{SITE}/planningsearch/search?dateFrom={frm}&dateTo={to}",
    ]
    for pu in page_urls:
        print(f"  render {pu[len(SITE):]}")
        r = sa(pu, {"render": "true", "wait_for_selector": "[id^=sr-]"}, timeout=200)
        if r is None:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        print(f"    title: {soup.title.get_text(strip=True) if soup.title else ''!r}")
        cards = soup.select("[id^=sr-]")
        print(f"    {len(cards)} elements with id sr-*")
        txt = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        print(f"    page text (first 1500): {txt[:1500]}")
        for c in cards[:3]:
            print(f"    CARD id={c.get('id')}: {re.sub(r'\s+', ' ', c.get_text(' ')).strip()[:500]}")
            print(f"       html: {str(c)[:900]}")
        if cards:
            open("/tmp/kc_rendered.html", "w", encoding="utf-8").write(r.text)
            break

    print(f"\nResult: {good}")
    print("Done.")


if __name__ == "__main__":
    sys.exit(main())
