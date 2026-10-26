"""Kensington & Chelsea recon, round 12 (council id 45). Writes nothing.

Round 11: every ScraperAPI route returns /planningsearch/api/cases/... as text/html utf-8 with bytes >= 0x80 re-encoded
(cp1252 -> utf-8) and five byte values lost, so the binary record stream cannot be decoded. The count prefix is right
(215 for 2 days) so the endpoint itself works. We need the bytes untouched, i.e. a transport that does not text-decode.

This round tries, from the uk-runner, using the small JSON endpoint first (caseCounts) and then /cases:
  T1  httpx direct with browser-like headers
  T2  curl_cffi (Chrome TLS fingerprint) direct
  T3  curl_cffi via the Webshare proxy
  T4  Playwright headless: open /planningsearch, then fetch() the API inside the page (arrayBuffer -> base64)
  T5  Playwright headless via Webshare, same in-page fetch
The first transport whose /cases reply decodes is used to print samples, the status mix, what the date range selects and
result caps.
"""
import base64
import json
import os
import sys
import time
from collections import Counter
from datetime import date, timedelta

import httpx

from kensington_probe11 import decode_cases, midnight_ms, d, summarise

SITE = "https://www.rbkc.gov.uk"
BASE = f"{SITE}/planningsearch/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-GB,en;q=0.9",
    "Referer": f"{SITE}/planningsearch", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}


def webshare_url():
    h, p = os.environ.get("WEBSHARE_PROXY_HOST"), os.environ.get("WEBSHARE_PROXY_PORT")
    u, w = os.environ.get("WEBSHARE_PROXY_USERNAME"), os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if not (h and p and u and w):
        return None
    return f"http://{u}:{w}@{h}:{p}"


def is_challenge(b):
    return b[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")) and (b"just a moment" in b[:6000].lower()
                                                                                   or b"enable javascript" in b[:6000].lower())


# ---- transports: each returns get(url) -> (status, content_type, bytes) and a close() ----
def t_httpx():
    c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=120)

    def get(url):
        r = c.get(url)
        return r.status_code, r.headers.get("content-type", ""), r.content
    return get, c.close


def t_curl(proxy=False):
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    px = None
    if proxy:
        w = webshare_url()
        if not w:
            raise RuntimeError("Webshare secrets not set")
        px = {"http": w, "https": w}

    def get(url):
        r = s.get(url, headers=HEADERS, proxies=px, timeout=120)
        return r.status_code, r.headers.get("content-type", ""), r.content
    return get, s.close


def t_playwright(proxy=False):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"]}
    if proxy:
        h, p = os.environ.get("WEBSHARE_PROXY_HOST"), os.environ.get("WEBSHARE_PROXY_PORT")
        u, w = os.environ.get("WEBSHARE_PROXY_USERNAME"), os.environ.get("WEBSHARE_PROXY_PASSWORD")
        if not (h and p and u and w):
            pw.stop()
            raise RuntimeError("Webshare secrets not set")
        kw["proxy"] = {"server": f"http://{h}:{p}", "username": u, "password": w}
    br = pw.chromium.launch(**kw)
    ctx = br.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London", viewport={"width": 1366, "height": 850})
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = ctx.new_page()
    try:
        page.goto(f"{SITE}/planningsearch", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"    goto error: {str(e)[:160]}")
    t0 = time.time()
    while time.time() - t0 < 30 and "just a moment" in (page.title() or "").lower():
        page.wait_for_timeout(1500)
    print(f"    page title after load: {page.title()!r}")
    if "just a moment" in (page.title() or "").lower():
        br.close()
        pw.stop()
        raise RuntimeError("Cloudflare challenge not passed")

    def get(url):
        res = page.evaluate("""async (u) => {
            const r = await fetch(u);
            const b = new Uint8Array(await r.arrayBuffer());
            let s = '';
            for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
            return {status: r.status, ct: r.headers.get('content-type') || '', b64: btoa(s)};
        }""", url)
        return res["status"], res["ct"], base64.b64decode(res["b64"])

    def close():
        try:
            br.close()
        finally:
            pw.stop()
    return get, close


TRANSPORTS = [
    ("T1 httpx direct", lambda: t_httpx()),
    ("T2 curl_cffi direct", lambda: t_curl(False)),
    ("T3 curl_cffi via Webshare", lambda: t_curl(True)),
    ("T4 Playwright direct", lambda: t_playwright(False)),
    ("T5 Playwright via Webshare", lambda: t_playwright(True)),
]


def explore(get):
    today = date.today()

    def cases(f, t, extra=""):
        st, ct, b = get(f"{BASE}/cases/everywhere?dateFrom={f}&dateTo={t}{extra}&sort=1")
        if st != 200:
            raise RuntimeError(f"HTTP {st}")
        return decode_cases(b)

    print("\n== samples: last 7 days")
    r7 = cases(midnight_ms(today - timedelta(days=7)), midnight_ms(today))
    print(f"  {len(r7)} records")
    summarise(r7)

    print("\n== what does the date range select? single days")
    day = today - timedelta(days=3)
    for label, a, b in (("day..day", midnight_ms(day), midnight_ms(day)),
                        ("day..day+1", midnight_ms(day), midnight_ms(day + timedelta(days=1)))):
        rr = cases(a, b)
        print(f"  -- {label} ({day}): {len(rr)} records")
        print(f"     registered dates {dict(Counter(d(r['dateRegistered'])[:10] for r in rr).most_common(6))}")
        print(f"     stage dates      {dict(Counter(d(r['currentStageDate'])[:10] for r in rr).most_common(6))}")
        print(f"     latest dates     {dict(Counter(d(r['latestDate'])[:10] for r in rr).most_common(6))}")
        print(f"     states           {dict(Counter(r['currentPlanningState'] for r in rr))}")

    print("\n== result caps")
    for days in (30, 90, 180):
        t0 = time.time()
        try:
            rr = cases(midnight_ms(today - timedelta(days=days)), midnight_ms(today))
            print(f"  last {days} days: {len(rr)} records ({time.time() - t0:.0f}s); unique refs "
                  f"{len(set(r['caseReference'] for r in rr))}; oldest registered "
                  f"{d(min(r['dateRegistered'] for r in rr))}")
        except Exception as e:
            print(f"  last {days} days: {e}")


def main():
    today = date.today()
    f2, t2 = midnight_ms(today - timedelta(days=2)), midnight_ms(today)
    counts_url = f"{BASE}/caseCounts/everywhere?dateFrom={f2}&dateTo={t2}&sort=1"
    cases_url = f"{BASE}/cases/everywhere?dateFrom={f2}&dateTo={t2}&sort=1"
    winner = None
    for name, factory in TRANSPORTS:
        print(f"\n== {name}")
        try:
            get, close = factory()
        except Exception as e:
            print(f"  setup failed: {str(e)[:200]}")
            continue
        try:
            st, ct, b = get(counts_url)
            tag = "CHALLENGE" if is_challenge(b) else ""
            print(f"  caseCounts: HTTP {st} {len(b)}B ct={ct!r} {tag} {b[:140]!r}")
            if st != 200 or is_challenge(b):
                continue
            st, ct, b = get(cases_url)
            print(f"  cases: HTTP {st} {len(b)}B ct={ct!r} first 24 bytes: {b[:24].hex(' ')}")
            try:
                recs = decode_cases(b)
                print(f"  DECODED {len(recs)} records  -> byte-exact transport")
                winner = (name, get, close)
                break
            except Exception as e:
                print(f"  decode failed: {e}")
        except Exception as e:
            print(f"  error: {str(e)[:250]}")
        finally:
            if not (winner and winner[0] == name):
                try:
                    close()
                except Exception:
                    pass
    if not winner:
        print("\nNo byte-exact transport. (Fallback: recover the lost bytes from structure, or render the page.)")
        return 0
    print(f"\nWinning transport: {winner[0]}")
    try:
        explore(winner[1])
    finally:
        winner[2]()
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
