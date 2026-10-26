"""Kensington & Chelsea recon, round 5 (council id 45). Writes nothing.

Round 4: the live search is https://www.rbkc.gov.uk/planningsearch, a JavaScript app titled 'Loading... | RBKC Planning
Portal' (old /PP/.. and /planning/scripts/.. URLs are 404). The planning pages also link an 'Open source planning data'
page and atlasplanning.uk/rbkc-committee. ScraperAPI's plain tier gets through Cloudflare.

This round, via ScraperAPI (about 12 requests, 100-150 credits):
  1. the raw /planningsearch HTML: scripts, inline config, any api/url strings
  2. /planningsearch rendered (render=true): text, forms, links
  3. each same-site script bundle: grep for api endpoints, graphql, fetch( calls
  4. the 'Open source planning data' page: text and every link (CSV/API/download)
  5. atlasplanning.uk/rbkc-committee
"""
import os
import re
import sys
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

KEY = os.environ.get("SCRAPERAPI_KEY", "")
BASE = "https://www.rbkc.gov.uk"
API = "https://api.scraperapi.com/"


def fetch(url, render=False, quiet=False):
    params = {"api_key": KEY, "url": url, "country_code": "gb"}
    if render:
        params["render"] = "true"
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, timeout=150, follow_redirects=True)
    except Exception as e:
        print(f"  ERROR {url}: {str(e)[:150]}")
        return None
    if not quiet:
        print(f"  GET {url}{' [render]' if render else ''} -> HTTP {r.status_code} {len(r.text)}b ({time.time() - t0:.0f}s)")
    return r


def show(label, text, n=700):
    t = re.sub(r"\s+", " ", text or "").strip()
    print(f"    {label}: {t[:n]}")


URL_RE = re.compile(r"""["'`](https?://[^"'`\s<>]{6,200}|/(?:api|graphql|odata|rest|v\d)[^"'`\s<>]{0,160})["'`]""", re.I)
KEYWORD_RE = re.compile(r"(api[/_.-]|graphql|fetch\(|axios|XMLHttpRequest|baseURL|apiUrl|endpoint|planning/|applications?/)", re.I)


def scan_js(name, js):
    urls = sorted(set(m.group(1) for m in URL_RE.finditer(js)))
    useful = [u for u in urls if not re.search(r"w3\.org|schema\.org|fonts\.g|googleapis\.com/css|reactjs\.org|github\.com|mozilla|npmjs|\.png|\.svg|\.woff|\.css", u, re.I)]
    print(f"    {name}: {len(js)} chars, {len(useful)} candidate URLs")
    for u in useful[:40]:
        print(f"        {u}")
    ctx_shown = 0
    for m in re.finditer(r"(baseURL|apiUrl|API_URL|apiBase|graphql|/api/[A-Za-z0-9_/\-{}.$]+|endpoint)", js):
        s = js[max(0, m.start() - 70): m.end() + 110].replace("\n", " ")
        print(f"        ctx: ...{s}...")
        ctx_shown += 1
        if ctx_shown >= 12:
            break


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1

    print("\n== 1. raw /planningsearch")
    r = fetch(f"{BASE}/planningsearch")
    scripts = []
    if r is not None:
        soup = BeautifulSoup(r.text, "html.parser")
        show("title", soup.title.get_text() if soup.title else "")
        print("    --- head/meta:")
        for m in soup.find_all("meta"):
            print(f"      meta {dict(m.attrs)}")
        for l in soup.find_all("link"):
            print(f"      link {dict(l.attrs)}")
        for s in soup.find_all("script"):
            if s.get("src"):
                scripts.append(urljoin(f"{BASE}/planningsearch", s["src"]))
                print(f"      script src={s['src']}")
            else:
                body = (s.string or s.get_text() or "").strip()
                print(f"      inline script ({len(body)} chars): {re.sub(r'\s+', ' ', body)[:600]!r}")
        print("    --- body (first 1500 chars of markup):")
        print("     ", re.sub(r"\s+", " ", str(soup.body))[:1500] if soup.body else "no body")
        open("/tmp/kc_planningsearch_raw.html", "w", encoding="utf-8").write(r.text)

    print("\n== 2. rendered /planningsearch")
    rr = fetch(f"{BASE}/planningsearch", render=True)
    if rr is not None:
        s2 = BeautifulSoup(rr.text, "html.parser")
        show("title", s2.title.get_text() if s2.title else "")
        show("text", s2.get_text(" "), 2500)
        print(f"    {len(s2.find_all('form'))} form(s), {len(s2.find_all('input'))} inputs, {len(s2.find_all('select'))} selects, {len(s2.find_all('table'))} tables")
        for el in s2.find_all(["input", "select", "button", "textarea"])[:40]:
            print(f"      {el.name} type={el.get('type')!r} name={el.get('name')!r} id={el.get('id')!r} placeholder={el.get('placeholder')!r} aria={el.get('aria-label')!r}")
        for a in s2.find_all("a", href=True)[:40]:
            print(f"      a {urljoin(BASE, a['href'])} [{a.get_text(' ', strip=True)[:50]}]")
        for s in s2.find_all("script", src=True):
            u = urljoin(f"{BASE}/planningsearch", s["src"])
            if u not in scripts:
                scripts.append(u)
        open("/tmp/kc_planningsearch_rendered.html", "w", encoding="utf-8").write(rr.text)

    print("\n== 3. script bundles")
    for u in scripts[:6]:
        if re.search(r"googletagmanager|google-analytics|cookie|jquery|gtag|clarity|hotjar", u, re.I):
            print(f"  skip {u}")
            continue
        j = fetch(u, quiet=True)
        if j is not None and j.status_code == 200:
            scan_js(u, j.text)
        else:
            print(f"  {u}: {j.status_code if j is not None else 'error'}")

    print("\n== 4. open source planning data page")
    o = fetch(f"{BASE}/planning-and-building-control/open-source-planning-data")
    if o is not None and o.status_code == 200:
        so = BeautifulSoup(o.text, "html.parser")
        main_el = so.find("main") or so.body
        show("text", main_el.get_text(" ") if main_el else so.get_text(" "), 3500)
        for a in (main_el or so).find_all("a", href=True):
            print(f"      a {urljoin(BASE, a['href'])} [{a.get_text(' ', strip=True)[:70]}]")

    print("\n== 5. atlasplanning.uk")
    a = fetch("https://atlasplanning.uk/rbkc-committee")
    if a is not None:
        sa = BeautifulSoup(a.text, "html.parser")
        show("title", sa.title.get_text() if sa.title else "")
        show("text", sa.get_text(" "), 1200)
        for x in sa.find_all("a", href=True)[:30]:
            print(f"      a {urljoin('https://atlasplanning.uk/', x['href'])} [{x.get_text(' ', strip=True)[:50]}]")
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
