"""Idox stale-seven probe (writes nothing).

Seven Idox councils stopped returning data between 12 Jul and 19 Sep 2026: Gosport, North East Derbyshire, Bolsover, Blaby,
Melton, Derbyshire Dales, Hinckley and Bosworth. For each one this asks the public-access search page three ways from the runner
(plain httpx, curl_cffi with a Chrome fingerprint, curl_cffi via Webshare) and reports status, title, server and whether the
reply is the real search form or a security challenge (Cloudflare, Incapsula, Akamai, generic block page).
"""
import os
import re
import sys
import time

import httpx

COUNCILS = {
    "Gosport": "https://publicaccess.gosport.gov.uk/online-applications",
    "NE Derbyshire": "https://planapps-online.ne-derbyshire.gov.uk/online-applications",
    "Bolsover": "https://publicaccess.bolsover.gov.uk/online-applications",
    "Blaby": "https://pa.blaby.gov.uk/online-applications",
    "Melton": "https://pa.melton.gov.uk/online-applications",
    "Derbyshire Dales": "https://planning.derbyshiredales.gov.uk/online-applications",
    "Hinckley & Bosworth": "https://pa.hinckley-bosworth.gov.uk/online-applications",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEAD = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"}
MARKS = [("cloudflare", r"just a moment|cf-chl|challenge-platform|attention required"), ("incapsula", r"incapsula|_incap_|request unsuccessful"),
         ("akamai", r"akamai|reference&#32;&#35;|access denied"), ("captcha", r"captcha|recaptcha|hcaptcha"),
         ("blocked", r"forbidden|you have been blocked|not authori[sz]ed")]


def webshare():
    h, p = os.environ.get("WEBSHARE_PROXY_HOST"), os.environ.get("WEBSHARE_PROXY_PORT")
    u, w = os.environ.get("WEBSHARE_PROXY_USERNAME"), os.environ.get("WEBSHARE_PROXY_PASSWORD")
    return f"http://{u}:{w}@{h}:{p}" if (h and p and u and w) else None


def verdict(status, text, headers):
    t = text.lower()
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = re.sub(r"\s+", " ", title.group(1)).strip()[:70] if title else ""
    found = [n for n, rx in MARKS if re.search(rx, t[:20000])]
    form = "searchCriteriaForm" in text or "name=\"searchCriteria" in text or "advancedSearchForm" in text
    kind = "REAL SEARCH FORM" if form else ("CHALLENGE/BLOCK " + ",".join(found) if found else "other page")
    return f"HTTP {status} {len(text)}B server={headers.get('server', '?')!r} title={title!r} -> {kind}"


def run():
    ws = webshare()
    try:
        from curl_cffi import requests as cr
        sess = cr.Session(impersonate="chrome")
    except Exception as e:
        sess = None
        print(f"curl_cffi unavailable: {e}")
    plain = httpx.Client(headers=HEAD, follow_redirects=True, timeout=40)
    for name, base in COUNCILS.items():
        print(f"\n== {name}  {base}")
        for path in ("/search.do?action=advanced&searchType=Application", "/"):
            url = base + path
            tries = [("httpx", lambda u: plain.get(u))]
            if sess is not None:
                tries.append(("curl_cffi", lambda u: sess.get(u, headers=HEAD, timeout=40)))
                if ws:
                    tries.append(("curl_cffi+webshare", lambda u: sess.get(u, headers=HEAD, proxies={"http": ws, "https": ws}, timeout=60)))
            for label, fn in tries:
                t0 = time.time()
                try:
                    r = fn(url)
                    print(f"  {path[:34]:34s} {label:19s} {verdict(r.status_code, r.text, r.headers)} ({time.time() - t0:.0f}s)")
                except Exception as e:
                    print(f"  {path[:34]:34s} {label:19s} ERROR {type(e).__name__}: {str(e)[:100]}")
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(run() or 0)
