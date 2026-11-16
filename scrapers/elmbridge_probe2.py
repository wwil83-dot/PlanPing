#!/usr/bin/env python3
"""PlanFind - Elmbridge probe 2 (2026-11-16). Probe 1 showed the results list is a plain GET and carries only number / address /
proposal / link (no status, no dates). This checks (a) plain HTTP works without a browser, (b) the detail page fields, (c) paging,
(d) what a one-day received / decided search returns."""
import re, sys
from datetime import date, timedelta
from urllib.parse import urlencode
import httpx
from bs4 import BeautifulSoup

BASE = "https://emaps.elmbridge.gov.uk/ebc_planning.aspx"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "Accept-Language": "en-GB,en;q=0.9"}


def results_url(kind, a, b, page=1, recs=500):
    q = {"pageno": page, "template": "AdvancedSearchResultsTab.tmplt", "requestType": "parseTemplate", "USRN:PARAM": "", "apptype:PARAM": "",
         "status:PARAM": "", "decision:PARAM": "", "ward:PARAM": "", "txt_search:PARAM": "",
         "daterec_from:PARAM": a if kind == "rec" else "", "daterec_to:PARAM": b if kind == "rec" else "",
         "datedec_from:PARAM": a if kind == "dec" else "", "datedec_to:PARAM": b if kind == "dec" else "",
         "pagerecs": recs, "orderxyz:PARAM": "REG_DATE_DT:DESCENDING", "SearchType:PARAM": "Advanced"}
    return BASE + "?" + urlencode(q)


def rows(html):
    out = []
    for tr in BeautifulSoup(html, "html.parser").select("table tr"):
        td = tr.find_all("td")
        if len(td) >= 4:
            a = td[3].find("a")
            out.append((td[0].get_text(strip=True), td[1].get_text(" ", strip=True), td[2].get_text(" ", strip=True)[:70], a["href"] if a else None))
    return out


def main():
    c = httpx.Client(headers=H, timeout=60, follow_redirects=True, verify=False)
    today = date.today()
    print("=== (a)/(d) plain HTTP, DECIDED last 10 days, 500 per page ===")
    r = c.get(results_url("dec", (today - timedelta(days=10)).isoformat(), today.isoformat()))
    print("HTTP", r.status_code, "len", len(r.text))
    dec = rows(r.text)
    print("decided rows:", len(dec))
    for x in dec[:3]:
        print("  ", x[:3])
    print("\n=== RECEIVED yesterday only ===")
    y = (today - timedelta(days=1)).isoformat()
    r = c.get(results_url("rec", y, y))
    rec = rows(r.text)
    print("received rows:", len(rec))
    print("\n=== (c) paging: RECEIVED last 60 days at 50 per page ===")
    seen = set()
    for pg in (1, 2, 3):
        r = c.get(results_url("rec", (today - timedelta(days=60)).isoformat(), today.isoformat(), page=pg, recs=50))
        rr = rows(r.text)
        refs = {x[0] for x in rr}
        print(f"page {pg}: {len(rr)} rows, {len(refs - seen)} new refs, first {rr[0][0] if rr else None}")
        seen |= refs
        m = re.search(r"(\d+)\s*(?:records?|results?|applications?)\s*found|of\s+(\d+)", r.text, re.I)
        if m and pg == 1:
            print("   count text:", m.group(0))
    print("\n=== (b) DETAIL pages ===")
    for label, lst in (("decided", dec[:3]), ("undecided (received yesterday)", rec[:1])):
        for ref, addr, prop, href in lst:
            if not href:
                continue
            r = c.get(href.replace("&amp;", "&"))
            soup = BeautifulSoup(r.text, "html.parser")
            print(f"\n--- {label}: {ref} HTTP {r.status_code} len {len(r.text)}")
            pairs = []
            for tr in soup.find_all("tr"):
                th, td = tr.find("th"), tr.find("td")
                if th and td:
                    pairs.append((th.get_text(" ", strip=True), td.get_text(" ", strip=True)[:90]))
            for dt in soup.find_all("dt"):
                dd = dt.find_next_sibling("dd")
                if dd:
                    pairs.append((dt.get_text(" ", strip=True), dd.get_text(" ", strip=True)[:90]))
            for p in pairs[:40]:
                print("   ", p)
            if not pairs:
                txt = " ".join(soup.get_text(" ").split())
                print("   (no th/td pairs) text:", txt[:1200])
            open(f"/tmp/elmbridge_probe2_detail_{ref.replace('/', '_')}.html", "w").write(r.text)


main()
