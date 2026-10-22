#!/usr/bin/env python3
"""
PlanFind - 'Planning Register' family PROBE: South Hams, West Devon, Wealden (writes NOTHING to the DB).

North Devon's portal (Search/Advanced, Planning/Display/<n>) is one product; url_check showed the same
engine (page title 'Application search - ...', /Disclaimer?returnUrl=...) on
  https://southhams.planning-register.co.uk   https://westdevon.planning-register.co.uk
  https://planning.wealden.gov.uk              (weekly list at /Planning/WeeklyList/)
Plain HTTP works on all three (Cloudflare / IIS, status 200). New compared with North Devon: a
/Disclaimer page that must be accepted first. For each host this probe (stdlib, ~25 requests, 1 s apart):
  1. loads /Disclaimer?returnUrl=%2FSearch%2FAdvanced, prints its form, accepts it
  2. loads /Search/Advanced: every field name (esp. date fields)
  3. loads the weekly list page (Wealden's known entry point) - text, links, forms
  4. runs a RECEIVED window and a DETERMINED/ISSUED window search: result count, table header, rows
  5. tries per-page 50 and page 2
  6. opens one detail page and prints its labelled text
Send the whole log back.
"""
import datetime as dt
import re
import sys

import northdevon_probe as P  # fetch / strip / parse_forms / show_forms / build_payload (full URLs ok)

HOSTS = [("South Hams", "https://southhams.planning-register.co.uk"),
         ("West Devon", "https://westdevon.planning-register.co.uk"),
         ("Wealden", "https://planning.wealden.gov.uk")]
strip, hr = P.strip, P.hr


def token(page):
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page) or \
        re.search(r'value="([^"]+)"[^>]*name="__RequestVerificationToken"', page)
    return m.group(1) if m else ""


def rows_of(page):
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = [strip(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, flags=re.I | re.S)]
        href = re.search(r'href="([^"]*/Display/[^"]*)"', tr, flags=re.I)
        if cells:
            out.append((cells, href.group(1) if href else ""))
    return out


def summary(page):
    m = re.search(r"returned\s+(\d+)\s+results?.{0,120}", strip(page), flags=re.I)
    return m.group(0)[:160] if m else None


def accept_disclaimer(base):
    st, fin, page, ct = P.fetch(base + "/Disclaimer?returnUrl=%2FSearch%2FAdvanced", headers={"Referer": base + "/"})
    print("disclaimer status", st, "final", fin, "len", len(page))
    forms = P.show_forms(page, "Disclaimer")
    # the accept form is the one that is not the site-search form
    cand = [f for f in forms if re.search(r"disclaimer|accept|agree", f[0] + " ".join(x["name"] + x["value"] for x in f[1]), re.I)] or forms
    if not cand:
        print("no form on disclaimer page; maybe a link:", re.findall(r'href="([^"]*(?:[Aa]ccept|[Aa]gree)[^"]*)"', page)[:5])
        return False
    tag, fields = cand[-1]
    action = (re.search(r'action="([^"]*)"', tag, flags=re.I) or [None, ""])[1] or "/Disclaimer?returnUrl=%2FSearch%2FAdvanced"
    action = action if action.startswith("http") else base + action
    data = P.build_payload(fields, {})
    for f in fields:  # include the submit button (named or not)
        if f["type"] == "submit" and f["name"]:
            data.append((f["name"], f["value"] or "Accept"))
    if not any(k == "__RequestVerificationToken" for k, _ in data):
        data.append(("__RequestVerificationToken", token(page)))
    print("accept POST ->", action, [(k, v[:20]) for k, v in data])
    st, fin, res, ct = P.fetch(action, data, headers={"Referer": base + "/Disclaimer"})
    print("accept status", st, "final", fin, "len", len(res))
    return True


def probe(name, base):
    hr(f"===== {name}: {base} =====")
    accept_disclaimer(base)

    hr(f"{name} 2. /Search/Advanced")
    st, fin, page, ct = P.fetch(base + "/Search/Advanced", headers={"Referer": base + "/"})
    print("status", st, "final", fin, "len", len(page), "| title:", strip(re.search(r"<title>(.*?)</title>", page, re.S | re.I).group(1)) if re.search(r"<title>", page, re.I) else "")
    forms = P.show_forms(page, "Advanced")
    sf = max(forms, key=lambda f: len(f[1])) if forms else None
    if not sf or len(sf[1]) < 6:
        print("!! search form not found (disclaimer not accepted?). first 400 chars:", strip(page)[:400])

    hr(f"{name} 3. Weekly list page")
    st, fin, w, ct = P.fetch(base + "/Planning/WeeklyList/", headers={"Referer": base + "/"})
    print("status", st, "final", fin, "len", len(w))
    P.show_forms(w, "WeeklyList")
    print(strip(re.sub(r"(?is)<(script|style).*?</\1>", " ", w))[:900])
    for h in sorted(set(re.findall(r'href="(/(?:Search|Planning)/[^"]*)"', w)))[:20]:
        print("   link:", h)

    if not sf or len(sf[1]) < 6:
        return
    tag, fields = sf
    action = (re.search(r'action="([^"]*)"', tag, flags=re.I) or [None, "/Search/Results"])[1]
    action = action if action.startswith("http") else base + action
    names = [f["name"] for f in fields if f["name"] and f["type"] in ("text", "date")]
    print("text fields:", names)

    def pick(kind_re, dir_re):
        for n in names:
            if re.search(kind_re, n, re.I) and not re.search(r"appeal", n, re.I) and re.search(dir_re, n, re.I):
                return n
        return None
    today = dt.date.today()
    d1, d2 = (today - dt.timedelta(days=14)).strftime("%d/%m/%Y"), today.strftime("%d/%m/%Y")

    def run(label, kind_re):
        f_from, f_to = pick(kind_re, r"from|start"), pick(kind_re, r"to$|end")
        print(f"\n--- {label} window {d1}..{d2}: from-field {f_from} to-field {f_to}")
        if not f_from:
            return None
        st, fin, adv, ct = P.fetch(base + "/Search/Advanced", headers={"Referer": base + "/"})
        tok = token(adv)
        over = {f_from: d1}
        if f_to:
            over[f_to] = d2
        data = P.build_payload(fields, over)
        data = [(k, tok if k == "__RequestVerificationToken" else v) for k, v in data]
        st, fin, res, ct = P.fetch(action, data, headers={"Referer": base + "/Search/Advanced"})
        rs = rows_of(res)
        print("status", st, "final", fin, "|", summary(res), "| rows", len(rs))
        hdr = [c for c, h in rs if not h][:1]
        print("header cells:", hdr)
        for cells, href in [r for r in rs if r[1]][:4]:
            print("   ", cells, href)
        return res, rs, tok

    got = run("RECEIVED", r"receiv")
    if got:
        res, rs, tok = got
        st, fin, r50, ct = P.fetch(base + "/Search/ChangeResultsPerPage", [("__RequestVerificationToken", tok), ("resultsPerPage", "50")],
                                   headers={"Referer": base + "/Search/Results"})
        r50rows = [r for r in rows_of(r50) if r[1]]
        print("per-page 50: status", st, "final", fin, "rows", len(r50rows), "|", summary(r50))
        tot = re.search(r"returned\s+(\d+)", strip(r50))
        if tot and int(tot.group(1)) > 50:
            st, fin, p2, ct = P.fetch(base + "/Search/Results/2/50")
            print("page 2 of 50:", st, "rows", len([r for r in rows_of(p2) if r[1]]))
        link = next((h for c, h in r50rows if h), "")
        if link:
            hr(f"{name} 6. Detail page {link}")
            st, fin, d, ct = P.fetch(link if link.startswith("http") else base + link)
            txt = strip(re.sub(r"(?is)<(script|style).*?</\1>", " ", d))
            i = txt.find("Summary")
            print("status", st, "len", len(d))
            print(txt[max(0, i - 600): i + 2300])
    run("DETERMINED/ISSUED", r"determin|issue|decision")


def main():
    only = sys.argv[1:] or None
    for name, base in HOSTS:
        if only and name not in only:
            continue
        try:
            probe(name, base)
        except Exception as e:
            print(f"!! {name} probe crashed: {type(e).__name__}: {e}")
    print("\nDone.")


if __name__ == "__main__":
    main()
