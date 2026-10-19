#!/usr/bin/env python3
"""
PlanFind - North Devon PROBE, round 3 (writes NOTHING to the database).

Round 2 showed the Advanced Search form (POST /Search/Results) has these usable fields:
  DateReceivedFrom/To, DateDeterminedFrom/To (DD/MM/YYYY), SearchPlanning checkbox (+hidden false),
  AdvancedSearch=True, __RequestVerificationToken.  No Date Valid field (valid date is only a results column).
Scripts reveal two POST endpoints: /Search/ChangeResultsPerPage (resultsPerPage 10/25/50) and
/Search/PageNumberChange (page).  My round-2 field-matching was wrong, so this round posts the right fields.

Checks: received-window search, determined-window search, the true results-page text/rows/forms,
per-page 50, paging, and one detail page's labelled fields.  Reuses helpers from northdevon_probe.py.
"""
import datetime as dt
import re

import northdevon_probe as P

fetch, strip, hr = P.fetch, P.strip, P.hr


def token(page):
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page) or \
        re.search(r'value="([^"]+)"[^>]*name="__RequestVerificationToken"', page)
    return m.group(1) if m else ""


def adv(tok, **kw):
    d = [("__RequestVerificationToken", tok), ("AdvancedSearch", "True"),
         ("SearchPlanning", "true"), ("SearchPlanning", "false"),
         ("SearchEnforcement", "false"), ("SearchAppeals", "false"), ("Outstanding", "false")]
    d += [(k, v) for k, v in kw.items()]
    return d


def results_info(page):
    txt = strip(page)
    for m in re.finditer(r"[^.]{0,80}(?:result|showing|page \d+ of)[^.]{0,120}", txt, flags=re.I):
        print("   TEXT:", m.group(0).strip()[:200])
        break
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = [strip(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, flags=re.I | re.S)]
        href = re.search(r'href="([^"]*/Planning/Display/[^"]*)"', tr, flags=re.I)
        if cells:
            rows.append((cells, href.group(1) if href else ""))
    return rows


def main():
    today = dt.date.today()
    f, t = (today - dt.timedelta(days=14)).strftime("%d/%m/%Y"), today.strftime("%d/%m/%Y")

    hr(f"A. RECEIVED window {f} .. {t}")
    st, fin, page, ct = fetch("/Search/Advanced")
    tok = token(page)
    print("token len", len(tok))
    st, fin, res, ct = fetch("/Search/Results", adv(tok, DateReceivedFrom=f, DateReceivedTo=t))
    print("status", st, "final", fin, "len", len(res))
    rows = results_info(res)
    print("table rows:", len(rows))
    for r in rows[:6]:
        print("   ", r)
    print("--- page text around results (first 900 chars of visible text) ---")
    print(strip(res)[:900])
    print("--- forms on results page ---")
    forms = P.show_forms(res, "Results")
    print("--- /Search/ and /Planning/ links ---")
    for h in sorted(set(re.findall(r'href="(/(?:Search|Planning)/[^"]*)"', res)))[:25]:
        print("   ", h)

    hr("B. Results per page = 50")
    tok2 = token(res) or tok
    st, fin, r50, ct = fetch("/Search/ChangeResultsPerPage", [("__RequestVerificationToken", tok2), ("resultsPerPage", "50")])
    print("status", st, "final", fin, "len", len(r50))
    rows50 = results_info(r50)
    print("rows after change:", len(rows50))
    if not rows50:
        st, fin, r50, ct = fetch("/Search/Results")
        rows50 = results_info(r50)
        print("rows on GET /Search/Results:", len(rows50))

    hr("C. Paging")
    refs = lambda rs: [c[0][1] if len(c[0]) > 1 else c[0] for c in rs]
    st, fin, p2, ct = fetch("/Search/Results/2")
    r2 = results_info(p2)
    print("page 2 status", st, "rows", len(r2))
    print("page1 refs:", refs(rows50)[:5], "...", refs(rows50)[-2:])
    print("page2 refs:", refs(r2)[:5])
    print("overlap:", set(refs(rows50)) & set(refs(r2)))
    st, fin, p3, ct = fetch("/Search/PageNumberChange", [("__RequestVerificationToken", tok2), ("page", "2")])
    print("PageNumberChange status", st, "rows", len(results_info(p3)))

    hr(f"D. DETERMINED window {f} .. {t}")
    st, fin, page, ct = fetch("/Search/Advanced")
    tok = token(page)
    st, fin, rd, ct = fetch("/Search/Results", adv(tok, DateDeterminedFrom=f, DateDeterminedTo=t))
    rowsd = results_info(rd)
    print("status", st, "rows", len(rowsd))
    for r in rowsd[:4]:
        print("   ", r)
    print(strip(rd)[:500])

    hr("E. One detail page, labelled fields")
    link = next((h for c, h in rows50 if h), "")
    print("detail link:", link)
    if link:
        st, fin, d, ct = fetch(link)
        print("status", st, "len", len(d))
        txt = strip(re.sub(r"(?is)<(script|style).*?</\1>", " ", d))
        i = txt.find("Application Number")
        print(txt[max(0, i - 100): i + 2500])

    print("\nDone.")


if __name__ == "__main__":
    main()
