#!/usr/bin/env python3
"""
PlanFind - Waverley API PROBE, round 2 (writes NOTHING to the database).

Round 1 found the planning360 portal (Civica W2) loads its data from a JSON API:
  POST {BASE}/w2webparts/Resource/Civica/Handler.ashx/keyobject/pagedsearch
       {"refType":"GFPlanning","fromRow":1,"toRow":11,"searchFields":{"FullTextSearch":"WA/2026"},"NoTotalRows":true}
  POST .../keyobject/search   {"refType":"GFPlanning","fromRow":1,"toRow":1,"keyNumb":"552987","keyText":"Subject"}
  GET  .../keyobject/getsearchcriteria/GFPlanning

This probe (stdlib only, ~40 polite requests, 1s apart) finds out:
  A. every searchable field name (esp. the date fields)
  B. every field a record returns (reference, address, proposal, dates, status, decision, ward...)
  C. whether plain HTTP works without a browser
  D. how many rows one request may return
  E. which date filters actually work (and what each date field means)
  F. what the detail call adds
Send the whole log back.
"""
import datetime as dt
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request

BASE = "https://planning360.waverley.gov.uk:4443"
H = BASE + "/w2webparts/Resource/Civica/Handler.ashx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


def call(path, body=None, url=None):
    url = url or (H + path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers={
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/planning/search-applications",
        "User-Agent": UA})
    try:
        with opener.open(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        return None, f"ERROR {e}"
    time.sleep(1)
    try:
        return status, json.loads(raw)
    except Exception:
        return status, raw[:400]


def search(fields, frm=1, to=10, total=False):
    return call("/keyobject/pagedsearch", {"refType": "GFPlanning", "fromRow": frm, "toRow": to,
                                           "searchFields": fields, "NoTotalRows": not total})


def items_of(ko):
    return {i.get("FieldName"): i for i in ko.get("Items", [])}


def show_record(ko, maxlen=110):
    for k, v in ko.items():
        if k != "Items":
            print(f"  [{k}] = {str(v)[:maxlen]}")
    for it in ko.get("Items", []):
        print(f"  {str(it.get('FieldName')):<12}| {str(it.get('DataType')):<2}| {str(it.get('Label')):<30}| {str(it.get('Value'))[:maxlen]}")


def kos(resp):
    if isinstance(resp, dict):
        return resp.get("KeyObjects") or []
    if isinstance(resp, list):
        return resp
    return []


def parse_date(s):
    s = str(s or "").strip()
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s[:len(dt.datetime.now().strftime(f))], f).date()
        except Exception:
            pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    return None


def main():
    today = dt.date.today()
    fmt = "%d/%m/%Y"

    hr("C. Plain HTTP (no browser): start page for cookies")
    try:
        req = urllib.request.Request(BASE + "/planning", headers={"User-Agent": UA})
        with opener.open(req, timeout=45) as r:
            print("status", r.status, "len", len(r.read()))
        print("cookies:", [c.name for c in cj])
    except Exception as e:
        print("start page failed:", e)
    time.sleep(1)

    hr("A. Search criteria (every searchable field)")
    st, crit = call("/keyobject/getsearchcriteria/GFPlanning")
    print("status", st)
    date_fields = []
    if isinstance(crit, dict):
        print("top-level keys:", list(crit.keys()))
        for si in crit.get("SearchItems", []):
            d = si.get("Display", {})
            line = f"  {str(d.get('FieldName')):<16}| type {si.get('DataType')} | {d.get('Label')} | visible {si.get('Visible')}"
            extra = {k: v for k, v in si.items() if k not in ("Display", "DataType", "Visible")}
            if extra:
                line += f" | extra {json.dumps(extra)[:140]}"
            print(line)
            if si.get("DataType") == "D" or "date" in str(d.get("Label", "")).lower():
                date_fields.append((d.get("FieldName"), d.get("Label")))
    else:
        print(crit)
    print("date-like fields:", date_fields)

    hr("B. One full list record (FullTextSearch=WA/2026), with totals")
    st, resp = search({"FullTextSearch": "WA/2026"}, 1, 3, total=True)
    print("status", st)
    if isinstance(resp, dict):
        print("top-level keys:", list(resp.keys()))
        for k, v in resp.items():
            if k != "KeyObjects":
                print(f"  {k} = {str(v)[:200]}")
    recs = kos(resp)
    print("records returned:", len(recs))
    if recs:
        print("--- FIRST RECORD, every field ---")
        show_record(recs[0])
    else:
        print(str(resp)[:600])

    hr("B2. Default ordering (rows 1-10) and paging stability")
    st, r1 = search({"FullTextSearch": "WA/2026"}, 1, 10)
    st, r2 = search({"FullTextSearch": "WA/2026"}, 11, 20)

    def brief(rs):
        out = []
        for ko in kos(rs):
            it = items_of(ko)
            ref = it.get("SDescription", {}).get("Value")
            dts = [f"{i.get('Label')}={i.get('Value')}" for i in ko.get("Items", []) if i.get("DataType") == "D"]
            out.append(f"{ref} {' '.join(dts)[:120]}")
        return out
    b1, b2 = brief(r1), brief(r2)
    print("rows 1-10:"); [print("  ", x) for x in b1]
    print("rows 11-20:"); [print("  ", x) for x in b2]
    refs1 = {x.split()[0] for x in b1}
    print("overlap between pages:", refs1 & {x.split()[0] for x in b2})

    hr("D. Page size")
    for to in (50, 200, 500):
        st, rs = search({"FullTextSearch": "WA/2026"}, 1, to)
        n = len(kos(rs))
        print(f"toRow={to}: status {st}, records {n}")
        if n == 0:
            print("   ", str(rs)[:200])

    hr("E. Date filters (from 14 days ago to today)")
    d1, d2 = (today - dt.timedelta(days=14)).strftime(fmt), today.strftime(fmt)
    names = [f for f, _ in date_fields if f]
    if "SDate3" not in names:
        names.append("SDate3")
    for fn in names[:12]:
        base_fn = re.sub(r"(From|To)$", "", fn)
        body = {base_fn + "From": d1, base_fn + "To": d2}
        st, rs = search(body, 1, 500)
        rows = kos(rs)
        vals = []
        for ko in rows:
            it = items_of(ko).get(base_fn)
            if it:
                vals.append(it.get("Value"))
        dts = [parse_date(v) for v in vals if parse_date(v)]
        print(f"{base_fn:<10} filter {d1}..{d2}: status {st}, records {len(rows)}"
              + (f", that field min {min(dts)} max {max(dts)}" if dts else f", sample values {vals[:3]}"))
        if rows:
            lab = items_of(rows[0]).get(base_fn, {}).get("Label")
            print(f"           label of {base_fn}: {lab}")

    hr("E2. Wider window: last 120 days on the date field(s) that worked")
    d1w = (today - dt.timedelta(days=120)).strftime(fmt)
    for fn in names[:12]:
        base_fn = re.sub(r"(From|To)$", "", fn)
        st, rs = search({base_fn + "From": d1w, base_fn + "To": d2}, 1, 500)
        n = len(kos(rs))
        print(f"{base_fn:<10} 120d: status {st}, records {n}" + ("  (== 500, capped?)" if n >= 500 else ""))

    hr("F. Detail call for the first record")
    if recs:
        ko = recs[0]
        keynumb = ko.get("KeyNumb") or ko.get("KeyNo") or ko.get("KeyNumber")
        keytext = ko.get("KeyText") or "Subject"
        print("keyNumb guess:", keynumb, "keyText:", keytext, "| record non-Items keys:", [k for k in ko if k != "Items"])
        if keynumb:
            st, det = call("/keyobject/search", {"refType": "GFPlanning", "fromRow": 1, "toRow": 1,
                                                  "keyNumb": str(keynumb), "keyText": keytext})
            print("status", st)
            dl = det if isinstance(det, list) else kos(det)
            if dl:
                print("--- DETAIL RECORD, every field ---")
                show_record(dl[0])
            else:
                print(str(det)[:500])

    hr("G. A decided application (Decision date search, last 14 days via SDate3 as the weekly-decisions link uses)")
    st, rs = search({"SDate3From": d1, "SDate3To": d2}, 1, 3)
    for ko in kos(rs)[:2]:
        print("--- record ---")
        show_record(ko)

    print("\nDone.")


if __name__ == "__main__":
    main()
