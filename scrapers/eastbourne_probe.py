#!/usr/bin/env python3
"""
PlanFind - Eastbourne API PROBE, round 2 (writes NOTHING to the database).

Round 1 found that Eastbourne's planning search is Civica W2 (same family as Waverley), but
on the shared Lewes/Eastbourne site:
  page host   : https://www.lewes-eastbourne.gov.uk   (behind a Cloudflare managed challenge:
                plain HTTP got 403, headless Chromium got through)
  API host    : https://gtest.lewes-eastbourne.gov.uk/civica/Resource/Civica/Handler.ashx/...
  search type : APPPlanCase  (Waverley used GFPlanning)
  weekly-list links use the query names ApplicationValidDateFrom/To and DecisionDateFrom/To.

This probe finds out (stdlib first, ~35 polite requests, 1s apart):
  A. does the API answer plain HTTP from the runner?  (if not, it repeats everything through
     headless Chromium by calling fetch() from the planning page)
  B. every searchable field (esp. date fields)
  C. every field a record returns; is the council Eastbourne only?
  D. page size, paging, newest-first ordering
  E. which date filters work, and what each date means
  F. the detail call
Send the whole log back.
"""
import datetime as dt
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request

WWW = "https://www.lewes-eastbourne.gov.uk"
H = "https://gtest.lewes-eastbourne.gov.uk/civica/Resource/Civica/Handler.ashx"
REF = "APPPlanCase"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


# ---- two transports with the same two methods ------------------------------------------
class PlainHttp:
    name = "plain HTTP"

    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def _do(self, req):
        try:
            with self.op.open(req, timeout=60) as r:
                raw, st = r.read().decode("utf-8", "replace"), r.status
        except urllib.error.HTTPError as e:
            raw, st = e.read().decode("utf-8", "replace"), e.code
        except Exception as e:
            return None, f"ERROR {e}"
        time.sleep(1)
        try:
            return st, json.loads(raw)
        except Exception:
            return st, raw[:300]

    def _hdr(self):
        return {"Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest", "Referer": WWW + "/planning",
                "Origin": WWW, "User-Agent": UA}

    def get(self, path):
        return self._do(urllib.request.Request(H + path, headers=self._hdr()))

    def post(self, path, body):
        return self._do(urllib.request.Request(H + path, data=json.dumps(body).encode(),
                                               method="POST", headers=self._hdr()))

    def close(self):
        pass


class BrowserFetch:
    """Calls the API from inside the planning page, so Cloudflare cookies/origin are genuine."""
    name = "headless Chromium fetch()"

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        ctx = self.browser.new_context(user_agent=UA, locale="en-GB", ignore_https_errors=True)
        self.page = ctx.new_page()
        self.page.goto(WWW + "/planning", wait_until="networkidle", timeout=60000)
        print("browser ready at", self.page.url, "title:", self.page.title())

    def _fetch(self, path, body=None):
        js = """async ([url, body]) => {
            const opt = body ? {method:'POST', headers:{'Content-Type':'application/json; charset=UTF-8',
                'Accept':'application/json, text/javascript, */*; q=0.01','X-Requested-With':'XMLHttpRequest'},
                body: JSON.stringify(body)} : {headers:{'Accept':'application/json'}};
            const r = await fetch(url, opt);
            const t = await r.text();
            return [r.status, t];
        }"""
        try:
            st, txt = self.page.evaluate(js, [H + path, body])
        except Exception as e:
            return None, f"ERROR {e}"
        time.sleep(1)
        try:
            return st, json.loads(txt)
        except Exception:
            return st, txt[:300]

    def get(self, path):
        return self._fetch(path)

    def post(self, path, body):
        return self._fetch(path, body)

    def close(self):
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass


# ---- helpers -----------------------------------------------------------------------------
def kos(resp):
    if isinstance(resp, dict):
        return resp.get("KeyObjects") or []
    return resp if isinstance(resp, list) else []


def items_of(ko):
    return {i.get("FieldName"): i for i in ko.get("Items", [])}


def show_record(ko, maxlen=110):
    for k, v in ko.items():
        if k != "Items":
            print(f"  [{k}] = {str(v)[:maxlen]}")
    for it in ko.get("Items", []):
        print(f"  {str(it.get('FieldName')):<22}| {str(it.get('Label')):<30}| {str(it.get('Value'))[:maxlen]}")


def pdate(s):
    s = str(s or "").strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return dt.date(int(m[3]), int(m[2]), int(m[1]))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    return None


def run(t):
    today = dt.date.today()
    fmt = "%d/%m/%Y"

    def search(fields, frm=1, to=10, total=False):
        return t.post("/keyobject/pagedsearch", {"refType": REF, "fromRow": frm, "toRow": to,
                                                  "searchFields": fields, "NoTotalRows": not total})

    hr(f"A/B. Search criteria via {t.name}")
    st, crit = t.get(f"/keyobject/getsearchcriteria/{REF}")
    print("status", st)
    if not isinstance(crit, dict):
        print("not JSON:", str(crit)[:300])
        return False
    date_fields = []
    for si in crit.get("SearchItems", []):
        d = si.get("Display", {})
        extra = {k: v for k, v in si.items() if k not in ("Display", "DataType", "Visible")}
        print(f"  {str(d.get('FieldName')):<24}| {si.get('DataType')} | {str(d.get('Label')):<34}| vis {si.get('Visible')}"
              + (f" | {json.dumps(extra)[:110]}" if extra else ""))
        if si.get("DataType") == "D" and si.get("Visible"):
            date_fields.append(d.get("FieldName"))

    hr("C. First records (FullTextSearch, newest default), with totals")
    st, resp = search({"FullTextSearch": "BN21 4TW"}, 1, 3, total=True)
    print("status", st, "| top keys:", list(resp.keys()) if isinstance(resp, dict) else str(resp)[:200])
    if isinstance(resp, dict):
        print("TotalRows:", resp.get("TotalRows"))
    recs = kos(resp)
    print("records:", len(recs))
    if recs:
        print("--- FIRST RECORD, every field ---")
        show_record(recs[0])

    hr("D. Page size / paging / ordering (no filter except a recent valid-date window)")
    d1, d2 = (today - dt.timedelta(days=14)).strftime(fmt), today.strftime(fmt)
    vf = next((f for f in date_fields if re.match(r"ApplicationValidDate", f or "")), None)
    base = re.sub(r"(From|To)$", "", vf) if vf else "ApplicationValidDate"
    win = {base + "From": d1, base + "To": d2}
    for to in (10, 100, 500):
        st, rs = search(win, 1, to)
        print(f"toRow={to}: status {st}, records {len(kos(rs))}" + ("" if kos(rs) else f"  {str(rs)[:200]}"))
    st, p1 = search(win, 1, 5)
    st, p2 = search(win, 6, 10)
    r1 = [items_of(k).get("ApplicationReference", items_of(k).get("SDescription", {})).get("Value") for k in kos(p1)]
    r2 = [items_of(k).get("ApplicationReference", items_of(k).get("SDescription", {})).get("Value") for k in kos(p2)]
    print("rows 1-5 :", r1)
    print("rows 6-10:", r2, "| overlap:", set(r1) & set(r2))

    hr("E. Every date filter (14-day window) - count and what that field's values look like")
    seen = set()
    for f in date_fields:
        b = re.sub(r"(From|To)$", "", f or "")
        if not b or b in seen:
            continue
        seen.add(b)
        st, rs = search({b + "From": d1, b + "To": d2}, 1, 500)
        rows = kos(rs)
        vals = []
        for ko in rows:
            for k, it in items_of(ko).items():
                if k == b or k.lower() == b.lower():
                    vals.append(it.get("Value"))
        ds = [pdate(v) for v in vals if pdate(v)]
        print(f"{b:<30} status {st}, records {len(rows)}"
              + (f", own-field min {min(ds)} max {max(ds)}" if ds else
                 f", (no same-named field in record; sample first record keys: {list(items_of(rows[0]))[:6] if rows else '-'})"))

    hr("E2. Decision window (weekly-list style DecisionDateFrom/To)")
    st, rs = search({"DecisionDateFrom": d1, "DecisionDateTo": d2}, 1, 500)
    rows = kos(rs)
    print("status", st, "records", len(rows))
    for ko in rows[:2]:
        print("--- decided record ---")
        show_record(ko)

    hr("F. Detail call for the first record")
    if recs:
        ko = recs[0]
        kn = ko.get("KeyNumber") or ko.get("KeyNo")
        kt = ko.get("KeyText") or ""
        print("keyNumber:", kn, "keyText:", repr(kt))
        for body in ({"refType": REF, "fromRow": 1, "toRow": 1, "keyNumb": str(kn), "keyText": kt},
                     {"refType": REF, "fromRow": 1, "toRow": 1, "keyNumb": str(kn)}):
            st, det = t.post("/keyobject/search", body)
            dl = det if isinstance(det, list) else kos(det)
            print("detail body", body, "-> status", st, "items", (len(dl[0].get("Items", [])) if dl else 0))
            if dl:
                show_record(dl[0])
                break
            print("   ", str(det)[:300])
    return True


def main():
    plain = PlainHttp()
    ok = run(plain)
    plain.close()
    if not ok:
        hr("PLAIN HTTP DID NOT WORK - repeating through headless Chromium")
        b = BrowserFetch()
        try:
            run(b)
        finally:
            b.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
