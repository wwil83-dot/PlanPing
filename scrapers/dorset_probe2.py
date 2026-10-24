#!/usr/bin/env python3
"""
PlanFind - Dorset PROBE round 2 (writes NOTHING to the DB).

Round 1: plain HTTP works. disclaimer.aspx accepts a POST (button ctl00$ContentPlaceHolder1$btnAccept), then
advsearch.aspx is a Telerik/ASP.NET WebForms page. My date search returned 'Page 1 of 33900' = no date filter
applied, for two reasons: (1) I used btnSearch2 (the first search panel) - the dates sit in the panel with
btnSearch3 (Received / Issued) - and (2) the dates are Telerik RadDatePickers: each has a text box
(txtDateReceivedFrom), a '$dateInput' box and _ClientState JSON hidden fields. Results page: 'Page 1 of N',
items 'P/HOU/2026/05815 Location: ... Proposal: ... Decision: ... Decision Date: ... View this application',
RadDataPager postbacks (...lvResults$RadDataPager1$ctl01$ctl00 ...).
This round: try the date search several ways, with btnSearch3, and report 'Page 1 of N' (a 14-day window
should be a few dozen pages at most); then read one results page's HTML structure, try the pager postback,
and open one detail page. Send the whole log back.
"""
import datetime as dt
import json
import re

import northdevon_probe as P

BASE = "https://planning.dorsetcouncil.gov.uk"
strip, hr = P.strip, P.hr
HDR = {"Referer": BASE + "/"}
CP = "ctl00$ContentPlaceHolder1$"
CPI = "ctl00_ContentPlaceHolder1_"


def clean(page):
    return strip(re.sub(r"(?is)<(script|style).*?</\1>", " ", page))


def accept():
    st, fin, page, ct = P.fetch(BASE + "/advsearch.aspx", headers=HDR)
    forms = P.parse_forms(page)
    if "disclaimer" in fin.lower():
        tag, fields = forms[0]
        data = P.build_payload(fields, {})
        data.append((CP + "btnAccept", "Accept"))
        url = BASE + "/disclaimer.aspx?returnURL=%2fadvsearch.aspx%3fAspxAutoDetectCookieSupport%3d1"
        st, fin, res, ct = P.fetch(url, data, headers={"Referer": url})
        print("disclaimer accepted:", st, fin)


def state(d, tag="dateInput"):
    s = d.strftime("%Y-%m-%d-00-00-00")
    return json.dumps({"enabled": True, "emptyMessage": "", "validationText": s, "valueAsString": s,
                       "minDateStr": "1980-01-01-00-00-00", "maxDateStr": "2099-12-31-00-00-00",
                       "lastSetTextBoxValue": d.strftime("%d/%m/%Y")}, separators=(",", ":"))


def date_overrides(prefix, d, variant):
    """prefix e.g. 'txtDateReceivedFrom'"""
    text = d.strftime("%d/%m/%Y")
    o = {CP + prefix + "$dateInput": text}
    if variant == 2:
        o[CP + prefix] = d.strftime("%Y-%m-%d")
        o[CPI + prefix + "_dateInput_ClientState"] = state(d)
        o[CPI + prefix + "_ClientState"] = json.dumps({"minDateStr": "1980-01-01-00-00-00", "maxDateStr": "2099-12-30-00-00-00"},
                                                      separators=(",", ":"))
    if variant == 3:
        o[CP + prefix] = d.strftime("%Y-%m-%d")
        o[CPI + prefix + "_dateInput_ClientState"] = state(d)
    return o


def search(variant, kind, d1, d2, button="btnSearch3"):
    st, fin, adv, ct = P.fetch(BASE + "/advsearch.aspx", headers=HDR)
    forms = P.parse_forms(adv)
    tag, fields = max(forms, key=lambda f: len(f[1]))
    a, b = ("txtDateReceivedFrom", "txtDateReceivedTo") if kind == "received" else ("txtDateIssuedFrom", "txtDateIssuedTo")
    over = {}
    over.update(date_overrides(a, d1, variant))
    over.update(date_overrides(b, d2, variant))
    data = P.build_payload(fields, over)
    data.append((CP + button, "Search"))
    return P.fetch(BASE + "/advsearch.aspx", data, headers={"Referer": BASE + "/advsearch.aspx"})


def pages_of(res):
    m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", clean(res))
    return m.groups() if m else None


def refs_of(res):
    return re.findall(r"\b(P/[A-Z]{2,5}/\d{4}/\d{4,6}|\d/\d{4}/\d{4,6}/[A-Z]+)\b", clean(res))[:40]


def main():
    accept()
    today = dt.date.today()
    d1, d2 = today - dt.timedelta(days=14), today
    best = None
    hr(f"A. Date search variants, window {d1:%d/%m/%Y}..{d2:%d/%m/%Y}")
    for kind in ("received", "issued"):
        for variant in (1, 2, 3):
            st, fin, res, ct = search(variant, kind, d1, d2)
            pg = pages_of(res)
            print(f"{kind:<9} variant {variant}: status {st} final {fin.rsplit('/',1)[-1]} len {len(res)} 'Page x of N': {pg} first refs: {refs_of(res)[:4]}")
            if pg and int(pg[1]) < 200 and kind == "received" and best is None:
                best = (variant, res)
    if not best:
        print("\n!! no variant narrowed the results. Showing the control names around the received date picker:")
        st, fin, adv, ct = P.fetch(BASE + "/advsearch.aspx", headers=HDR)
        for m in re.finditer(r"[^<]{0,200}txtDateReceivedFrom[^>]{0,300}", adv):
            print("  ", m.group(0)[:500])
            break
        print("\nDone.")
        return
    variant, res = best
    print(f"\nusing variant {variant} for the rest")

    hr("B. First results page structure")
    i = res.find("View this application")
    print(res[max(0, i - 1800): i + 300])
    print("\nlinks to detail:", re.findall(r'href="([^"]*(?:details|application|Details)[^"]*)"', res, re.I)[:6])
    print("pager targets:", sorted(set(re.findall(r"__doPostBack\(&#39;([^&]*RadDataPager[^&]*)&#39;", res)))[:14])
    print("pager labels:", re.findall(r'class="rdpPageLink[^"]*"[^>]*>\s*<span>([^<]*)</span>', res)[:14] or re.findall(r"RadDataPager1[^>]*>([^<]{1,6})<", res)[:14])
    print("page-size select / other controls:", re.findall(r'name="(ctl00\$ContentPlaceHolder1\$[^"]*)"', res)[:20])

    hr("C. Pager postback (page 2)")
    forms = P.parse_forms(res)
    tag, fields = max(forms, key=lambda f: len(f[1]))
    targets = sorted(set(re.findall(r"__doPostBack\(&#39;([^&]*RadDataPager[^&]*)&#39;", res)))
    first = refs_of(res)[:3]
    for tg in targets[:4]:
        data = P.build_payload(fields, {"__EVENTTARGET": tg, "__EVENTARGUMENT": ""})
        st, fin, r2, ct = P.fetch(BASE + "/searchresults.aspx", data, headers={"Referer": BASE + "/searchresults.aspx"})
        print(f"target {tg[-14:]}: status {st} 'Page x of N' {pages_of(r2)} refs {refs_of(r2)[:3]} (page 1 was {first})")

    hr("D. Detail page")
    link = re.search(r'href="([^"]*)"[^>]*>\s*View this application', res, re.I)
    if link:
        u = link.group(1).replace("&amp;", "&")
        u = u if u.startswith("http") else BASE + "/" + u.lstrip("/")
        st, fin, d, ct = P.fetch(u, headers={"Referer": BASE + "/searchresults.aspx"})
        print("url:", u, "status", st, "len", len(d))
        print(clean(d)[:2600])
    else:
        print("no 'View this application' link found")
    print("\nDone.")


if __name__ == "__main__":
    main()
