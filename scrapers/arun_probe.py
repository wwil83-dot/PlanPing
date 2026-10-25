#!/usr/bin/env python3
"""
PlanFind - Arun District Council PROBE (writes NOTHING to the DB).

Arun (councils.id 5, labelled 'other'). Your note: https://www.arun.gov.uk/planning-application-search/ has a
form built into the page (received-from/to and decided-from/to, results below). A search also turned up
https://www1.arun.gov.uk/planning-application-finder . Plain HTTP first (stdlib, ~20 requests, 1 s apart):
  1. both pages: status, redirects, title, text head, every form (fields, selects, action), iframes,
     script/link URLs that look like search endpoints
  2. a RECEIVED-date window search (last 14 days) through the best form found: count line, columns, rows,
     paging links, detail links
  3. one detail page's labelled text
Send the whole log back.
"""
import datetime as dt
import re
from urllib.parse import urljoin

import northdevon_probe as P

PAGES = ["https://www.arun.gov.uk/planning-application-search/",
         "https://www1.arun.gov.uk/planning-application-finder"]
strip, hr = P.strip, P.hr


def clean(page):
    return strip(re.sub(r"(?is)<(script|style).*?</\1>", " ", page))


def rows_of(page):
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = [strip(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, flags=re.I | re.S)]
        href = re.search(r'href="([^"]+)"', tr, flags=re.I)
        if cells:
            out.append((cells, href.group(1) if href else ""))
    return out


def main():
    today = dt.date.today()
    d1, d2 = today - dt.timedelta(days=14), today
    best = None
    for url in PAGES:
        hr(f"1. {url}")
        st, fin, page, ct = P.fetch(url, headers={"Referer": url})
        print("status", st, "final", fin, "len", len(page) if isinstance(page, str) else page)
        if st != 200 or not isinstance(page, str):
            print("   not reachable:", str(page)[:200])
            continue
        t = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        print("title:", strip(t.group(1)) if t else None)
        print("iframes:", re.findall(r'<iframe[^>]*src="([^"]+)"', page, re.I)[:6])
        print("scripts:", [s for s in re.findall(r'<script[^>]*src="([^"]+)"', page, re.I) if not re.search(r"jquery|bootstrap|google|analytics|cookie", s, re.I)][:12])
        print("links:", sorted(set(l for l in re.findall(r'href="([^"]*(?:search|planning|application|finder|portal|idox|northgate|civica|publicaccess)[^"]*)"', page, re.I)))[:25])
        i = clean(page).lower().find("received")
        print("text near 'received':", clean(page)[max(0, i - 300): i + 500] if i >= 0 else clean(page)[:500])
        forms = P.show_forms(page, "page")
        cand = [f for f in forms if sum(1 for x in f[1] if re.search(r"date|recei|decid|from|to", x["name"] + x["id"], re.I)) >= 2]
        if cand and best is None:
            best = (url, fin, page, max(cand, key=lambda f: len(f[1])))

    if not best:
        print("\n!! no form with date fields found in the plain HTML - probably built by JavaScript or in an iframe;")
        print("   next step would be a headless-browser probe. Done.")
        return
    url, fin, page, (tag, fields) = best
    hr(f"2. Date search through the form on {url}")
    action = (re.search(r'action="([^"]*)"', tag, flags=re.I) or [None, ""])[1].replace("&amp;", "&")
    action = urljoin(fin, action) if action else fin
    method = (re.search(r'method="([^"]*)"', tag, flags=re.I) or [None, "get"])[1].lower()
    print("action", action, "method", method)
    dates = [f for f in fields if f["type"] in ("text", "date") and re.search(r"date|recei|decid|valid|from|to", f["name"] + f["id"] + f["placeholder"], re.I)]
    print("date-like fields:", [(f["name"], f["id"], f["placeholder"]) for f in dates])
    a = next((f["name"] for f in dates if re.search(r"recei", f["name"] + f["id"], re.I) and re.search(r"from|start|1$", f["name"] + f["id"], re.I)), None) \
        or next((f["name"] for f in dates if re.search(r"from|start", f["name"] + f["id"], re.I)), None)
    b = next((f["name"] for f in dates if re.search(r"recei", f["name"] + f["id"], re.I) and re.search(r"to$|end|2$|until", f["name"] + f["id"], re.I)), None) \
        or next((f["name"] for f in dates if f["name"] != a and re.search(r"to$|end", f["name"] + f["id"], re.I)), None)
    print("chosen from/to:", a, b)
    if not a:
        print("could not pick the date fields; use the list above. Done.")
        return
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        over = {a: d1.strftime(fmt)}
        if b:
            over[b] = d2.strftime(fmt)
        data = P.build_payload(fields, over)
        btn = next((f for f in fields if f["type"] in ("submit", "image") and f["name"] and re.search(r"search|find|submit", f["name"] + f["value"], re.I)), None)
        if btn:
            data.append((btn["name"], btn["value"] or "Search"))
        print(f"\n--- format {fmt}: posting {[(k, v[:20]) for k, v in data if v][:12]}")
        if method == "get":
            from urllib.parse import urlencode
            st, fin2, res, ct = P.fetch(action + ("&" if "?" in action else "?") + urlencode(data), headers={"Referer": fin})
        else:
            st, fin2, res, ct = P.fetch(action, data, headers={"Referer": fin})
        print("status", st, "final", fin2, "len", len(res) if isinstance(res, str) else res)
        if not isinstance(res, str):
            continue
        txt = clean(res)
        m = re.search(r"(\d[\d,]*)\s+(?:results?|records?|applications?)[^.]{0,80}|showing[^.]{0,80}|page\s+\d+\s+of\s+\d+|no (?:results|records)[^.]{0,60}", txt, re.I)
        print("count line:", m.group(0) if m else None)
        rs = rows_of(res)
        print("table rows:", len(rs))
        for cells, href in rs[:6]:
            print("   ", [c[:40] for c in cells][:8], "->", href[:100])
        print("paging links:", sorted(set(re.findall(r'href="([^"]*(?:page|Page|next|Next|start)[^"]*)"', res)))[:8])
        print("detail-ish links:", sorted(set(re.findall(r'href="([^"]*(?:detail|Detail|application|ref=|id=|keyVal)[^"]*)"', res)))[:6])
        if not rs:
            print("page text:", txt[:700])
        else:
            link = next((h for c, h in rs if h and not h.startswith(("javascript", "#"))), "")
            if link:
                hr("3. Detail page")
                u = urljoin(fin2, link.replace("&amp;", "&"))
                st, fin3, d, ct = P.fetch(u, headers={"Referer": fin2})
                print("url", u, "status", st)
                print(clean(d)[:2500] if isinstance(d, str) else d)
            break
    print("\nDone.")


if __name__ == "__main__":
    main()
