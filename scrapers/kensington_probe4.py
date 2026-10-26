"""Kensington & Chelsea recon, round 4 (council id 45). Writes nothing.

Round 3: ScraperAPI's plain tier (10 credits) gets through Cloudflare; the origin then said /planning/scripts/weeklyform.asp
is a 404 (stale URL). Application pages exist at /PP/<yy>/<nnnnn>. This round finds the live search:
  1. fetch an application page, print every link and form (esp. 'Back to search results')
  2. fetch the planning landing pages and print their links and forms
  3. follow the most promising search/list links (max 8 extra fetches) and dump forms/links/tables
Total about 15 requests = about 150 credits.
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
SEEN = set()


def fetch(url, render=False):
    params = {"api_key": KEY, "url": url, "country_code": "gb"}
    if render:
        params["render"] = "true"
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, timeout=120, follow_redirects=True)
    except Exception as e:
        print(f"  ERROR {url}: {str(e)[:150]}")
        return None
    print(f"  GET {url} -> HTTP {r.status_code} {len(r.text)}b ({time.time() - t0:.0f}s)")
    return r


def show(label, text, n=700):
    t = re.sub(r"\s+", " ", text or "").strip()
    print(f"    {label}: {t[:n]}")


def dump_forms(soup):
    forms = soup.find_all("form")
    print(f"    {len(forms)} form(s)")
    for i, f in enumerate(forms):
        print(f"    --- form {i}: action={f.get('action')!r} method={f.get('method')!r} id={f.get('id')!r}")
        for el in f.find_all(["input", "select", "textarea", "button"]):
            if el.name == "select":
                opts = [(o.get("value"), o.get_text(strip=True)) for o in el.find_all("option")]
                print(f"        select name={el.get('name')!r} id={el.get('id')!r} {len(opts)} options")
                for v, t in opts[:8]:
                    print(f"            {v!r}: {t!r}")
                if len(opts) > 8:
                    print(f"            ... last: {opts[-1]!r}")
            else:
                print(f"        {el.name} type={el.get('type')!r} name={el.get('name')!r} id={el.get('id')!r} "
                      f"value={(el.get('value') or '')[:60]!r}")


def links(soup, url):
    out = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        out.append((urljoin(url, h), a.get_text(" ", strip=True)[:70]))
    return out


def look(url, label, print_all=True, render=False):
    SEEN.add(url)
    r = fetch(url, render)
    if r is None or r.status_code != 200:
        return None, []
    soup = BeautifulSoup(r.text, "html.parser")
    show("title", soup.title.get_text() if soup.title else "")
    ls = links(soup, url)
    if print_all:
        rel = [(u, t) for u, t in ls if re.search(r"plan|search|appl|list|decision|PP/|weekly|register|map", u + " " + t, re.I)]
        print(f"    {len(ls)} links, {len(rel)} relevant:")
        for u, t in rel[:45]:
            print(f"      {u}  [{t}]")
    dump_forms(soup)
    return soup, ls


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1

    print("\n== 1. application page")
    soup, ls = look(f"{BASE}/PP/24/05187", "detail")
    cand = []
    if soup is not None:
        show("text", soup.get_text(" "), 1800)
        for u, t in ls:
            if re.search(r"back to search|search result|search again|new search", t, re.I):
                cand.append((u, t))

    print("\n== 2. planning landing pages")
    for path in ["/planning", "/planning/", "/planning-and-building-control/planning",
                 "/planning/searches", "/planning/planningsearch", "/"]:
        s, l2 = look(BASE + path, path, print_all=True)
        if s is not None:
            for u, t in l2:
                if re.search(r"search|weekly|list|decision|application", t + " " + u, re.I) and "rbkc.gov.uk" in u:
                    cand.append((u, t))
        if len(SEEN) > 7:
            break

    print("\n== 3. follow promising links")
    followed = 0
    order = sorted(set(cand), key=lambda x: (0 if re.search(r"back to search", x[1], re.I) else 1, len(x[0])))
    for u, t in order:
        if u in SEEN or followed >= 8:
            continue
        if re.search(r"\.(pdf|jpg|png|docx?)$", u, re.I) or "/PP/" in u and "search" not in u.lower():
            continue
        print(f"\n  -> {t!r}")
        s, _ = look(u, t)
        followed += 1
        if s is not None:
            show("text", s.get_text(" "), 900)
            for ti, tb in enumerate(s.find_all("table")[:2]):
                trs = tb.find_all("tr")
                print(f"    table {ti}: {len(trs)} rows")
                for tr in trs[:3]:
                    show("row", tr.get_text(" | ", strip=True), 300)
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
