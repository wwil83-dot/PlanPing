"""Kensington & Chelsea recon (council id 45). Writes nothing.

RBKC publishes a classic-ASP "weekly list" form (planning/scripts/weeklyform.asp: pick an issue date, list goes back
six months). This probe:
  1. loads that form and dumps every <form>, input, select (with options) and planning-related link
  2. submits it for the most recent Fridays and shows the result structure (headers, first rows, links)
  3. opens the first application link it finds and dumps the labelled fields
  4. tries a few likely 'real' search URLs and reports status codes
Plain HTTP only (httpx + beautifulsoup4).
"""
import re
import sys
from datetime import date, timedelta
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

BASE = "https://www.rbkc.gov.uk"
FORM_URL = f"{BASE}/planning/scripts/weeklyform.asp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def show(label, text, n=700):
    t = re.sub(r"\s+", " ", text or "").strip()
    print(f"    {label}: {t[:n]}")


def dump_forms(soup, url):
    forms = soup.find_all("form")
    print(f"  {len(forms)} form(s) on {url}")
    for i, f in enumerate(forms):
        print(f"  --- form {i}: action={f.get('action')!r} method={f.get('method')!r} id={f.get('id')!r}")
        for el in f.find_all(["input", "select", "textarea", "button"]):
            if el.name == "select":
                opts = [(o.get("value"), o.get_text(strip=True)) for o in el.find_all("option")]
                print(f"      select name={el.get('name')!r} id={el.get('id')!r} {len(opts)} options")
                for v, t in opts[:12]:
                    print(f"          {v!r}: {t!r}")
                if len(opts) > 12:
                    print(f"          ... last: {opts[-1]!r}")
            else:
                print(f"      {el.name} type={el.get('type')!r} name={el.get('name')!r} id={el.get('id')!r} "
                      f"value={(el.get('value') or '')[:60]!r}")


def dump_links(soup, url, pat=r"plan|appl|search|weekly|list|decision", limit=40):
    seen = 0
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        h = a["href"]
        if re.search(pat, h + " " + txt, re.I):
            print(f"      link {urljoin(url, h)}  [{txt[:60]}]")
            seen += 1
            if seen >= limit:
                break


def fridays(n=4):
    d = date.today()
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return [d - timedelta(days=7 * i) for i in range(n)]


def main():
    c = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
                     follow_redirects=True, timeout=40)
    print("== 1. weekly form")
    r = c.get(FORM_URL)
    print(f"  GET {FORM_URL} -> {r.status_code} {len(r.text)} bytes, final url {r.url}")
    soup = BeautifulSoup(r.text, "html.parser")
    show("title", soup.title.get_text() if soup.title else "")
    show("body text", soup.get_text(" "), 1500)
    dump_forms(soup, str(r.url))
    dump_links(soup, str(r.url))

    print("\n== 2. submit the form")
    form = None
    for f in soup.find_all("form"):
        if f.find("select") or f.find("input", {"type": re.compile("text|date", re.I)}):
            form = f
            break
    results = []
    if form is None:
        print("  no usable form found")
    else:
        action = urljoin(str(r.url), form.get("action") or "")
        method = (form.get("method") or "get").lower()
        base = {}
        for el in form.find_all(["input", "select", "textarea"]):
            nm = el.get("name")
            if not nm:
                continue
            t = (el.get("type") or "").lower()
            if t in ("submit", "button", "image"):
                continue
            if el.name == "select":
                sel = el.find("option", selected=True) or (el.find_all("option") or [None])[0]
                base[nm] = (sel.get("value") if sel is not None else "") or ""
            elif t in ("checkbox", "radio"):
                if el.has_attr("checked"):
                    base[nm] = el.get("value", "on")
            else:
                base[nm] = el.get("value", "")
        print(f"  action={action} method={method} base fields={base}")
        # find the select that holds dates (options that look like dates)
        for el in form.find_all("select"):
            opts = [o.get("value") for o in el.find_all("option")]
            print(f"  select {el.get('name')}: first={opts[:3]} last={opts[-2:]}")
        for fd in fridays(3):
            variants = []
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %B %Y", "%d/%m/%y"):
                variants.append(fd.strftime(fmt))
            # prefer an exact option value if the form is a select
            chosen = None
            for el in form.find_all("select"):
                for o in el.find_all("option"):
                    ov = o.get("value") or o.get_text(strip=True)
                    for v in variants:
                        if v.lower() in ov.lower():
                            chosen = (el.get("name"), ov)
                            break
                    if chosen:
                        break
                if chosen:
                    break
            tried = [chosen] if chosen else []
            if not tried:
                names = [n for n in base if re.search(r"date|issue|week|from", n, re.I)] or list(base)[:1]
                for nm in names[:1]:
                    for v in variants[:3]:
                        tried.append((nm, v))
            for nm, v in tried:
                data = dict(base)
                data[nm] = v
                try:
                    rr = (c.post(action, data=data) if method == "post" else c.get(action, params=data))
                except Exception as e:
                    print(f"  {fd} {nm}={v!r}: ERROR {e}")
                    continue
                s2 = BeautifulSoup(rr.text, "html.parser")
                tables = s2.find_all("table")
                rows = s2.find_all("tr")
                print(f"  {fd} {nm}={v!r}: {rr.status_code} {len(rr.text)}b tables={len(tables)} rows={len(rows)} url={rr.url}")
                if len(rows) > 3 or len(rr.text) > 4000:
                    results.append((fd, rr, s2))
                    break
            if len(results) >= 2:
                break

    print("\n== 3. result structure")
    detail_url = None
    for fd, rr, s2 in results[:1]:
        print(f"  list for {fd} ({rr.url})")
        show("text", s2.get_text(" "), 1200)
        for ti, t in enumerate(s2.find_all("table")[:4]):
            rows = t.find_all("tr")
            print(f"   table {ti}: {len(rows)} rows")
            for tr in rows[:4]:
                show("row", tr.get_text(" | ", strip=True), 400)
        dump_links(s2, str(rr.url), pat=r".", limit=25)
        for a in s2.find_all("a", href=True):
            if re.search(r"ref|appl|detail|case|planning", a["href"], re.I) and not re.search(r"weeklyform", a["href"]):
                detail_url = urljoin(str(rr.url), a["href"])
                break
        open("/tmp/kc_weekly.html", "w", encoding="utf-8").write(rr.text)

    print("\n== 4. one detail page")
    if detail_url:
        rr = c.get(detail_url)
        print(f"  GET {detail_url} -> {rr.status_code}")
        s3 = BeautifulSoup(rr.text, "html.parser")
        show("title", s3.title.get_text() if s3.title else "")
        show("text", s3.get_text(" "), 2500)
        dump_forms(s3, detail_url)
        dump_links(s3, detail_url, limit=20)
    else:
        print("  no detail link found")

    print("\n== 5. candidate search URLs")
    for u in [
        "/planning/searches", "/planning/scripts/", "/planning/scripts/search.asp",
        "/planning/scripts/weeklylist.asp", "/planning/planningsearch", "/planning/planningsearch/",
        "/planning-and-building-control/planning/search-planning-applications",
        "/planning-and-building-control/planning/planning-applications",
        "/planning/planningarchiveviewer/search.aspx?SubCategoryID=29",
    ]:
        try:
            rr = c.get(BASE + u)
            s4 = BeautifulSoup(rr.text, "html.parser")
            print(f"  {u} -> {rr.status_code} {len(rr.text)}b  title={(s4.title.get_text(strip=True) if s4.title else '')[:80]!r} forms={len(s4.find_all('form'))}")
        except Exception as e:
            print(f"  {u} -> ERROR {e}")
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
