#!/usr/bin/env python3
"""
PlanFind - North Devon PROBE, round 2 (writes NOTHING to the database).

Round 1 (site_recon) found https://planning.northdevon.gov.uk is a server-rendered ASP.NET MVC
app ("Council Services"), no JSON API:
  GET  /                      Basic Search form: POST /Search/Results
                              fields: __RequestVerificationToken, SearchPlanning, SearchEnforcement,
                              SearchAppeals, QuickSearch
  GET  /Search/Advanced       Advanced Search (form not yet seen)
  POST /Search/Results -> 302 -> GET /Search/Results (10 per page), pages at /Search/Results/2
  GET  /Planning/Display/<n>  one application (HTML: Summary, Important Dates, Further Information)
  scripts on every page: exportResultsToCSV.js, resultsPerPage.js, sortTable.js

This probe (stdlib only, ~20 polite requests, 1s apart) finds out:
  A. the real Advanced Search form (every field, select options, action) - especially date fields
  B. what exportResultsToCSV.js and resultsPerPage.js do (endpoints!)
  C. whether a Date Valid range search works over plain HTTP (token + cookie), how many
     results/pages come back, and the result-row layout
  D. the same for a Decision Date range
  E. whether results-per-page can be raised, and whether a CSV export endpoint exists
Send the whole log back.
"""
import datetime as dt
import html
import http.cookiejar
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://planning.northdevon.gov.uk"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


def fetch(path_or_url, data=None, headers=None):
    url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url
    hdr = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
           "Referer": BASE + "/Search/Advanced"}
    if headers:
        hdr.update(headers)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST" if body is not None else "GET")
    try:
        with opener.open(req, timeout=60) as r:
            raw = r.read()
            final, status, ctype = r.geturl(), r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raw, final, status, ctype = e.read(), url, e.code, e.headers.get("Content-Type", "")
    except Exception as e:
        return None, url, f"ERROR {e}", ""
    time.sleep(1)
    return status, final, raw.decode("utf-8", "replace"), ctype


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_forms(page):
    """Return [(form_open_tag, [field dicts])] for every <form> in the page."""
    out = []
    for m in re.finditer(r"(<form\b[^>]*>)(.*?)</form>", page, flags=re.I | re.S):
        opener_tag, inner = m.group(1), m.group(2)
        fields = []
        for fm in re.finditer(r"<(input|select|textarea)\b([^>]*)>(.*?)(?=<(?:input|select|textarea|/form)\b|$)",
                              inner, flags=re.I | re.S):
            tag, attrs = fm.group(1).lower(), fm.group(2)

            def a(k):
                x = re.search(r'\b' + k + r'\s*=\s*"([^"]*)"', attrs, flags=re.I)
                return html.unescape(x.group(1)) if x else ""
            f = {"tag": tag, "name": a("name"), "id": a("id"), "type": a("type") or ("select" if tag == "select" else "text"),
                 "value": a("value"), "checked": bool(re.search(r"\bchecked\b", attrs, flags=re.I)),
                 "placeholder": a("placeholder")}
            if tag == "select":
                seg = re.search(r"<select\b[^>]*>(.*?)</select>", inner[fm.start():], flags=re.I | re.S)
                opts = re.findall(r'<option\b([^>]*)>(.*?)</option>', seg.group(1) if seg else "", flags=re.I | re.S)
                f["options"] = [(re.search(r'value="([^"]*)"', oa).group(1) if re.search(r'value="([^"]*)"', oa) else strip(ot),
                                 strip(ot), bool(re.search(r"\bselected\b", oa, flags=re.I))) for oa, ot in opts]
            fields.append(f)
        out.append((opener_tag, fields))
    return out


def show_forms(page, label):
    forms = parse_forms(page)
    print(f"--- {label}: {len(forms)} forms ---")
    for tag, fields in forms:
        print(" FORM", re.sub(r"\s+", " ", tag)[:200], f"({len(fields)} fields)")
        for f in fields:
            line = f"    {f['tag']:<8} name={f['name']!r:<34} id={f['id']!r:<22} type={f['type']:<9} value={f['value'][:30]!r}"
            if f["placeholder"]:
                line += f" ph={f['placeholder'][:30]!r}"
            if f["checked"]:
                line += " [checked]"
            print(line)
            if f["tag"] == "select":
                print("       options:", [o[1][:30] for o in f.get("options", [])][:16], "...")
    return forms


def parse_results(page):
    m = re.search(r"returned\s+(\d+)\s+results?.*?page\s+(\d+)\s+of\s+(\d+)\s*\((\d+)\s+results per page\)", strip(page), flags=re.I)
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = [strip(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, flags=re.I | re.S)]
        if cells:
            href = re.search(r'href="([^"]*Display/[^"]*)"', tr, flags=re.I)
            rows.append((cells, href.group(1) if href else ""))
    return (m.groups() if m else None), rows


def build_payload(fields, overrides):
    """Default values of every field, as a form post would send them, plus overrides."""
    data = []
    for f in fields:
        n = f["name"]
        if not n or f["type"] in ("submit", "button", "image", "reset"):
            continue
        if n in overrides:
            continue
        if f["type"] == "checkbox":
            if f["checked"]:
                data.append((n, f["value"] or "true"))
            continue
        if f["tag"] == "select":
            sel = [o for o in f.get("options", []) if o[2]]
            data.append((n, sel[0][0] if sel else (f["options"][0][0] if f.get("options") else "")))
            continue
        data.append((n, f["value"]))
    for k, v in overrides.items():
        data.append((k, v))
    return data


def main():
    today = dt.date.today()
    d1, d2 = (today - dt.timedelta(days=14)), today

    hr("A. Advanced Search page")
    st, final, page, ct = fetch("/Search/Advanced")
    print("status", st, "len", len(page), "final", final)
    forms = show_forms(page, "Advanced")
    main_form = max(forms, key=lambda f: len(f[1])) if forms else None

    hr("B. Scripts that may reveal endpoints")
    for js in ("exportResultsToCSV.js", "resultsPerPage.js", "sortTable.js", "pageChange.js"):
        st, final, body, ct = fetch(f"/Scripts/def/common/{js}")
        print(f"\n----- {js} (status {st}, {len(body)} chars) -----")
        print(body[:3000])

    if not main_form:
        print("no form found; stopping")
        return
    tag, fields = main_form
    action = (re.search(r'action="([^"]*)"', tag, flags=re.I) or [None, "/Search/Results"])[1]
    print("\nmain form action:", action)

    # --- find date fields heuristically --------------------------------------------------
    datef = [f for f in fields if f["type"] in ("text", "date") and re.search(r"date|valid|decision|receiv", f["name"] + f["id"], re.I)]
    print("date-like fields:", [(f["name"], f["id"], f["placeholder"]) for f in datef])

    def pick(pattern_kind, pattern_dir):
        for f in datef:
            nm = (f["name"] + " " + f["id"] + " " + f["placeholder"]).lower()
            if re.search(pattern_kind, nm) and re.search(pattern_dir, nm):
                return f["name"]
        return None

    def run_window(label, kind_re, tag_re):
        hr(f"{label}: {d1:%d/%m/%Y} .. {d2:%d/%m/%Y}")
        f_from = pick(kind_re, r"from|start|min|after|^(?!.*(to|end|max|before))")
        f_to = pick(kind_re, r"\bto\b|end|max|before|until|_to|to$")
        print("chosen from/to field names:", f_from, f_to)
        if not f_from:
            print("  could not identify the from-field; fill in manually from the field list above")
            return None
        over = {f_from: d1.strftime("%d/%m/%Y")}
        if f_to:
            over[f_to] = d2.strftime("%d/%m/%Y")
        data = build_payload(fields, over)
        print("posting:", [(k, v[:25]) for k, v in data if v])
        st, final, res, ct = fetch(action, data)
        print("status", st, "final", final, "len", len(res))
        summ, rows = parse_results(res)
        print("summary (results, page, pages, per page):", summ)
        for cells, href in rows[:4]:
            print("  row:", cells, href)
        return res, summ, rows

    out = run_window("C. DATE VALID window", r"valid", "")
    if out:
        res, summ, rows = out
        hr("C2. Results page structure (forms / selects / per-page controls / pagination links)")
        show_forms(res, "Results")
        for h in sorted(set(re.findall(r'href="(/Search/[^"]*)"', res)))[:20]:
            print("  link:", h)
        if summ and int(summ[2]) > 1:
            st, final, p2, ct = fetch("/Search/Results/2")
            s2, r2 = parse_results(p2)
            print("page 2:", s2, "rows", len(r2), [c[0][1] if len(c[0]) > 1 else c[0] for c in r2[:3]])
            first_p1 = [c[0][1] for c in rows if len(c[0]) > 1][:3]
            print("page-1 refs:", first_p1, "| overlap with page 2:", set(first_p1) & {c[0][1] for c in r2 if len(c[0]) > 1})

    out2 = run_window("D. DECISION DATE window", r"decision", "")
    if out2:
        _, summ2, rows2 = out2

    hr("E. Guess a results-per-page / export endpoints seen in the scripts")
    for path in ("/Search/ExportResults", "/Search/ExportToCSV", "/Search/Export", "/Search/ResultsPerPage"):
        st, final, body, ct = fetch(path)
        print(path, "->", st, ct[:40], strip(body)[:120] if isinstance(body, str) else "")

    print("\nDone.")


if __name__ == "__main__":
    main()
