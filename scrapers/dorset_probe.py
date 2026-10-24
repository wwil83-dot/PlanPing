#!/usr/bin/env python3
"""
PlanFind - Dorset Council PROBE (writes NOTHING to the DB).

Dorset Council (councils.id 368, labelled 'idox' but really a custom ASP.NET site):
  https://planning.dorsetcouncil.gov.uk/advsearch.aspx   (advanced search, behind a disclaimer)
Plain HTTP first (stdlib, ~20 requests, 1 s apart). This finds out:
  1. does the host answer plain HTTP at all (status / redirects / blocks)?
  2. the disclaimer: its form and whether a POST accepts it
  3. the advanced search form: every field (esp. date fields), selects, buttons
  4. a RECEIVED-date window search and a DECISION-date window search (last 14 days): count, columns, rows,
     paging controls (__doPostBack targets / links), detail links
  5. one detail page's labelled text
Send the whole log back.
"""
import datetime as dt
import re

import northdevon_probe as P  # fetch / strip / parse_forms / show_forms / build_payload

BASE = "https://planning.dorsetcouncil.gov.uk"
strip, hr = P.strip, P.hr
HDR = {"Referer": BASE + "/"}


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


def pick_button(fields, want):
    btns = [f for f in fields if f["type"] in ("submit", "button", "image") and f["name"]]
    for f in btns:
        if re.search(want, f["name"] + " " + f["value"], re.I):
            return f
    return btns[0] if btns else None


def post_form(page, action_url, fields, overrides, button):
    data = P.build_payload(fields, overrides)
    if button:
        data.append((button["name"], button["value"] or "Submit"))
    return P.fetch(action_url, data, headers={"Referer": action_url})


def form_action(tag, page_url):
    a = (re.search(r'action="([^"]*)"', tag, flags=re.I) or [None, ""])[1].replace("&amp;", "&")
    if not a:
        return page_url
    if a.startswith("http"):
        return a
    return BASE + (a if a.startswith("/") else "/" + a.lstrip("./"))


def main():
    hr("1. advsearch.aspx (cold)")
    st, fin, page, ct = P.fetch(BASE + "/advsearch.aspx", headers=HDR)
    print("status", st, "final", fin, "len", len(page), "ctype", ct)
    if st != 200 or not isinstance(page, str):
        print("!! not reachable over plain HTTP:", st, str(page)[:300])
        return
    t = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    print("title:", strip(t.group(1)) if t else None)
    print("text:", clean(page)[:900])
    forms = P.show_forms(page, "page")

    hr("2. disclaimer / accept (if present)")
    text = clean(page).lower()
    adv = None
    if re.search(r"disclaimer|i accept|i agree|terms and conditions", text) or all(len(f[1]) < 8 for f in forms):
        cand = [f for f in forms if pick_button(f[1], r"accept|agree|continue|enter")]
        if cand:
            tag, fields = cand[0]
            btn = pick_button(fields, r"accept|agree|continue|enter")
            url = form_action(tag, fin)
            print("accept: POST", url, "button", btn["name"], btn["value"])
            st2, fin2, res, ct2 = post_form(page, url, fields, {}, btn)
            print("status", st2, "final", fin2, "len", len(res))
            print(clean(res)[:500])
        else:
            print("no accept button found; links:", re.findall(r'href="([^"]*(?:ccept|gree|isclaimer)[^"]*)"', page)[:6])
    else:
        print("no disclaimer wording found on the first page")

    hr("3. advanced search form")
    st, fin, adv, ct = P.fetch(BASE + "/advsearch.aspx", headers=HDR)
    print("status", st, "final", fin, "len", len(adv))
    forms = P.show_forms(adv, "advsearch")
    if not forms:
        print("!! no form; text:", clean(adv)[:600])
        return
    tag, fields = max(forms, key=lambda f: len(f[1]))
    action = form_action(tag, fin)
    print("main form action:", action, "| fields:", len(fields))
    dates = [f for f in fields if f["type"] in ("text", "date") and re.search(r"date|dt|recei|valid|decis|issued|determin", f["name"] + f["id"], re.I)]
    print("date-like fields:", [(f["name"], f["id"], f["placeholder"]) for f in dates])
    submit = pick_button(fields, r"search|submit|find")
    print("submit button:", submit and (submit["name"], submit["value"]))

    today = dt.date.today()
    d1, d2 = (today - dt.timedelta(days=14)), today

    def which(kind_re, dir_re):
        for f in dates:
            nm = f["name"] + " " + f["id"]
            if re.search(kind_re, nm, re.I) and re.search(dir_re, nm, re.I) and not re.search("appeal", nm, re.I):
                return f["name"]
        return None

    def run(label, kind_re):
        a, b = which(kind_re, r"from|start|min|1$"), which(kind_re, r"to$|end|max|2$|until")
        print(f"\n--- {label}: {d1:%d/%m/%Y}..{d2:%d/%m/%Y} fields {a} / {b}")
        if not a:
            print("   could not identify the from-field; use the list above")
            return None
        # fresh viewstate each time
        st, fin, adv2, ct = P.fetch(BASE + "/advsearch.aspx", headers=HDR)
        f2 = P.parse_forms(adv2)
        tag2, fields2 = max(f2, key=lambda f: len(f[1]))
        over = {a: d1.strftime("%d/%m/%Y")}
        if b:
            over[b] = d2.strftime("%d/%m/%Y")
        st, fin, res, ct = post_form(adv2, form_action(tag2, fin), fields2, over, pick_button(fields2, r"search|submit|find"))
        print("status", st, "final", fin, "len", len(res))
        txt = clean(res)
        m = re.search(r"(\d+)\s+(?:results?|records?|applications?)[^.]{0,80}|showing[^.]{0,80}|no (?:results|records)[^.]{0,60}", txt, re.I)
        print("count line:", m.group(0) if m else None)
        rs = rows_of(res)
        print("rows:", len(rs))
        for cells, href in rs[:6]:
            print("   ", [c[:40] for c in cells][:8], "->", href[:90])
        pb = sorted(set(re.findall(r"__doPostBack\('([^']*)'", res)))
        print("postback targets:", pb[:14])
        pl = sorted(set(re.findall(r'href="([^"]*(?:page|Page|next|Next)[^"]*)"', res)))
        print("paging links:", pl[:8])
        if not rs:
            print("page text:", txt[:600])
        return res, rs

    got = run("RECEIVED", r"recei|valid|date1|reg")
    run("DECISION", r"decis|determin|issued")
    if got:
        res, rs = got
        link = next((h for c, h in rs if h and not h.startswith("javascript")), "")
        if link:
            hr("5. detail page")
            u = link.replace("&amp;", "&")
            u = u if u.startswith("http") else BASE + (u if u.startswith("/") else "/" + u)
            st, fin, d, ct = P.fetch(u, headers={"Referer": BASE + "/advsearch.aspx"})
            print("status", st, "final", fin, "len", len(d))
            print(clean(d)[:2500])
    print("\nDone.")


if __name__ == "__main__":
    main()
