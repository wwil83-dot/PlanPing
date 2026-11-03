"""Recon (no writes): Carmarthenshire weekly planning lists (Excel). Finds the file links on the page, downloads the newest
registered and decided lists and prints their layout."""
import io, os, re, sys
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

PAGE = "https://www.carmarthenshire.gov.wales/council-services/planning/search-for-a-planning-application/"
PROXIES = [p.strip() for p in os.environ.get("PROXY_URLS", "").split(",") if p.strip()]
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}


def get(url):
    try:
        r = httpx.get(url, headers=HEAD, timeout=40, follow_redirects=True)
        print(f"GET {url[-60:]} -> {r.status_code} ({len(r.content)} bytes) direct")
        if r.status_code == 200:
            return r.content
    except Exception as e:
        print(f"GET {url[-60:]} direct failed: {type(e).__name__}")
    from curl_cffi import requests as cr
    for i, px in enumerate(PROXIES[:4]):
        try:
            r = cr.get(url, impersonate="chrome", proxies={"http": px, "https": px}, timeout=60)
            print(f"GET {url[-60:]} -> {r.status_code} ({len(r.content)} bytes) via proxy {i}")
            if r.status_code == 200:
                return r.content
        except Exception as e:
            print(f"  proxy {i} failed: {type(e).__name__}")
    return None


html = get(PAGE)
if not html:
    sys.exit("page not reachable")
soup = BeautifulSoup(html, "html.parser")
print("\n--- headings ---")
for h in soup.find_all(re.compile("^h[1-4]$")):
    print(h.name, h.get_text(" ", strip=True)[:100])

print("\n--- every link to a spreadsheet, with the nearest heading and the control it sits in ---")
files = []
for a in soup.find_all("a", href=True):
    if re.search(r"\.xlsx?($|\?)", a["href"], re.I) or re.search(r"\.xlsx?", a.get_text(), re.I):
        h = a.find_previous(re.compile("^h[1-4]$"))
        sel = a.find_parent("select")
        files.append((h.get_text(" ", strip=True) if h else "", a.get_text(" ", strip=True), urljoin(PAGE, a["href"])))
for f in files:
    print(f)
print("\n--- <option> tags (dropdown lists) ---")
for o in soup.find_all("option"):
    print(repr(o.get_text(" ", strip=True)), "|", o.get("value"))

import openpyxl
seen = set()
for head, text, url in files:
    key = head or "?"
    if key in seen:
        continue
    seen.add(key)
    data = get(url)
    if not data:
        continue
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as e:
        print("cannot open:", e)
        continue
    print(f"\n===== {key} | {text} | {url}")
    for ws in wb.worksheets:
        print(f"sheet {ws.title!r}: {ws.max_row} rows x {ws.max_column} columns")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 8:
                break
            print("  ", [str(c)[:50] if c is not None else None for c in row])
