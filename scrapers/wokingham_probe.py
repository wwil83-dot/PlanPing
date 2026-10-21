#!/usr/bin/env python3
"""
PlanFind - Wokingham PROBE (writes NOTHING to the database).

Recon showed Wokingham runs classic ASP 'FastWebPL' (planning.wokingham.gov.uk/FastWebPL):
  welcome.asp  quick search: ApplicationNumber, AddressPrefix, Postcode, KeywordSearch -> POST results.asp
  results.asp  each result already shows: App No, address, description, Received Date, Date Valid,
               Decision Date, Decision, iLap ref; hidden RecCount, select 'Page' (7 pages for 121 hits)
  search.asp   advanced search (not yet seen)
  weeklylistapp.asp / weeklylistdec.asp   weekly lists
  detail.asp?AltRef=<n>  returned HTTP 500 when opened in a browser straight from the results link.

This probe (stdlib, ~15 requests, 1 s apart) finds out:
  A. the advanced search form (all fields, esp. date ranges)
  B. what the two weekly-list pages contain
  C. results paging (RecCount / Page) over plain HTTP
  D. whether detail.asp works over plain HTTP, with and without the session cookie
  E. a date-range search using the best-guess date fields from A
"""
import datetime as dt
import http.cookiejar
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import northdevon_probe as P  # reuse parse_forms / show_forms / strip

B = "https://planning.wokingham.gov.uk/FastWebPL/"
UA = P.UA
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
hr, strip = P.hr, P.strip


def fetch(path, data=None, referer=B + "welcome.asp"):
    url = path if path.startswith("http") else B + path
    hdr = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Referer": referer}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST" if body is not None else "GET")
    try:
        with op.open(req, timeout=60) as r:
            raw, st = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, st = e.read(), e.code
    except Exception as e:
        return f"ERROR {e}", ""
    time.sleep(1)
    return st, raw.decode("utf-8", "replace")


def blocks(page):
    txt = strip(page)
    parts = re.split(r"App\. No\.:", txt)[1:]
    out = []
    for p in parts:
        ref = p.split()[0] if p.split() else ""
        m = re.search(r"Received Date:\s*(\S*)\s*Date Valid:\s*(\S*)", p)
        dd = re.search(r"Decision Date:\s*(\S*)\s*Decision:\s*(.*?)(?:$|\s+App\.)", p)
        out.append((ref, m.groups() if m else None, dd.groups() if dd else None))
    return out


def main():
    today = dt.date.today()
    d1, d2 = (today - dt.timedelta(days=14)).strftime("%d/%m/%Y"), today.strftime("%d/%m/%Y")

    hr("A. Advanced search page search.asp")
    st, page = fetch("search.asp")
    print("status", st, "len", len(page))
    forms = P.show_forms(page, "search.asp")
    sf = max(forms, key=lambda f: len(f[1])) if forms else None

    hr("B. Weekly lists")
    for name in ("weeklylistapp.asp", "weeklylistdec.asp"):
        st, w = fetch(name)
        print(f"\n--- {name} status {st} len {len(w)} ---")
        P.show_forms(w, name)
        t = strip(re.sub(r"(?is)<(script|style).*?</\1>", " ", w))
        print(t[:1200])
        for h in re.findall(r'href="([^"]+)"', w)[:25]:
            if re.search(r"week|list|pdf|csv|\.asp", h, re.I):
                print("   link:", h)

    hr("C. Quick-search paging (KeywordSearch=2026)")
    qs = {"ApplicationNumber": "", "AddressPrefix": "", "Postcode": "", "KeywordSearch": "2026", "Submit": "Search"}
    st, r1 = fetch("results.asp", qs)
    b1 = blocks(r1)
    print("status", st, "blocks on page 1:", len(b1), [b[0] for b in b1][:12])
    print("sample block:", b1[0] if b1 else None)
    P.show_forms(r1, "results.asp")
    rc = re.search(r'name="RecCount"[^>]*value="(\d+)"', r1) or re.search(r'value="(\d+)"[^>]*name="RecCount"', r1)
    print("RecCount:", rc.group(1) if rc else None)
    for pg in ("2",):
        d = dict(qs); d.update({"RecCount": rc.group(1) if rc else "", "Page": pg})
        st, r2 = fetch("results.asp", d, referer=B + "results.asp")
        b2 = blocks(r2)
        print(f"page {pg} POST (same fields + Page): status {st}, blocks {len(b2)}", [b[0] for b in b2][:12],
              "| overlap with page 1:", set(x[0] for x in b1) & set(x[0] for x in b2))
    d = {"Page": "2", "RecCount": rc.group(1) if rc else ""}
    st, r3 = fetch("results.asp", d, referer=B + "results.asp")
    print("page 2 POST (Page+RecCount only): status", st, "blocks", len(blocks(r3)))

    hr("D. detail.asp over plain HTTP")
    ref = b1[0][0] if b1 else "262316"
    for label, url in (("with search querystring (as linked)", f"detail.asp?AltRef={ref}&ApplicationNumber=&AddressPrefix=&Postcode=&KeywordSearch=2026&Submit=Search"),
                       ("AltRef only", f"detail.asp?AltRef={ref}"),
                       ("ApplicationNumber", f"detail.asp?ApplicationNumber={ref}")):
        st, dpage = fetch(url, referer=B + "results.asp")
        print(f"{label}: status {st}, len {len(dpage)}")
        if st == 200:
            print(strip(re.sub(r"(?is)<(script|style).*?</\1>", " ", dpage))[:1500])
            break

    hr("E. Date-range search using advanced-search fields")
    if sf:
        tag, fields = sf
        action = (re.search(r'action="([^"]*)"', tag, flags=re.I) or [None, "results.asp"])[1]
        dfields = [f for f in fields if f["name"] and re.search(r"date|valid|recei|decid|from|to", f["name"], re.I)
                   and f["type"] in ("text", "date")]
        print("action:", action, "| date-like fields:", [f["name"] for f in dfields])
        base = P.build_payload(fields, {})
        print("default payload:", [(k, v[:20]) for k, v in base if v])
        # try every date-like pair guess: put d1 in fields containing 'from'/'start'/'1', d2 in 'to'/'end'/'2'
        over = {}
        for f in dfields:
            n = f["name"].lower()
            if re.search(r"valid|recei", n):
                over[f["name"]] = d1 if re.search(r"from|start|min|1$|_f", n) or not re.search(r"to|end|max|2$", n) else d2
        print("overrides tried (valid/received):", over)
        if over:
            st, rr = fetch(action, P.build_payload(fields, over), referer=B + "search.asp")
            bb = blocks(rr)
            print("status", st, "blocks", len(bb), [(b[0], b[1]) for b in bb[:4]])
            print(strip(rr)[300:700])
    print("\nDone.")


if __name__ == "__main__":
    main()
