#!/usr/bin/env python3
"""
PlanFind — Mole Valley API PROBE, round 4 (writes NOTHING to the database).

Round 3 found that the Statmap portal is an open JSON API:
  POST https://molevalley-publicportal.statmap.co.uk/horizoNext/api/publicportal/planningApplications/pageRequest
returning records with name (the reference), initialAppRef, address, proposal,
receivedDate, status, decision, decisionDate. No login or browser needed.

This probe makes a few polite direct calls (stdlib only, ~25 requests, 1s apart)
to learn what the scraper needs:
  A. how many records one request may return (pageSize)
  B. whether we can sort newest-first by receivedDate
  C. whether we can sort by decisionDate (to catch recent decisions)
  D. whether a date filter works, and in what form
  E. whether a detail endpoint exists
Send the whole log back.
"""
import json, time, urllib.request, urllib.error

BASE = "https://molevalley-publicportal.statmap.co.uk/horizoNext/api/publicportal/planningApplications"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SELECT = {"name": True, "initialAppRef": True, "address": True, "proposal": True,
          "receivedDate": True, "status": True, "decision": True, "id": True}


def call(url, body=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/plain, */*", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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
        return status, raw[:300]


def page(size=10, offset=0, order=None, items=None, adv=None):
    return {"select": SELECT, "pageSize": size, "offset": offset,
            "filter": {"parts": [{"filterItems": items if items is not None else
                                  [{"columnName": "searchType", "value": "Simple", "operator": "="}]}]},
            "order": order if order is not None else {},
            "advancedFilter": {"parts": adv or []}, "entityName": "P_APPLICATION"}


def show(label, res, n=6):
    status, data = res
    print(f"\n--- {label} ---")
    if isinstance(data, dict) and "records" in data:
        recs = data["records"]
        print(f"  HTTP {status}: {len(recs)} records, total={data.get('total')}")
        for r in recs[:n]:
            print(f"   {r.get('name')} | recv={r.get('receivedDate')} | status={r.get('status')} | "
                  f"decision={r.get('decision')} | decided={r.get('decisionDate')}")
        return recs
    print(f"  HTTP {status}: {str(data)[:300]}")
    return []


def main():
    print("PlanFind Mole Valley API probe, round 4 (no database writes)")
    url = BASE + "/pageRequest"

    show("A0 baseline, pageSize 10", call(url, page(10)), n=3)
    for size in (100, 500):
        recs = show(f"A pageSize {size}", call(url, page(size)), n=0)
        print(f"   → pageSize {size} returned {len(recs)}")

    show("B order receivedDate desc", call(url, page(8, order={"receivedDate": "desc"})))
    show("B2 order receivedDate desc (offset 8)", call(url, page(8, offset=8, order={"receivedDate": "desc"})))
    show("C order decisionDate desc", call(url, page(8, order={"decisionDate": "desc"})))

    # D. date filters — try several shapes; look at total and the dates returned
    cands = [
        ("receivedDate >= ISO", [{"columnName": "receivedDate", "value": "2026-10-01", "operator": ">="}]),
        ("receivedDate >= ISO T", [{"columnName": "receivedDate", "value": "2026-10-01T00:00:00", "operator": ">="}]),
        ("receivedDate >= DMY", [{"columnName": "receivedDate", "value": "01/10/2026", "operator": ">="}]),
        ("receivedDate gte", [{"columnName": "receivedDate", "value": "2026-10-01", "operator": "gte"}]),
        ("receivedDate GreaterThanOrEqual", [{"columnName": "receivedDate", "value": "2026-10-01", "operator": "GreaterThanOrEqual"}]),
        ("decisionDate >= ISO", [{"columnName": "decisionDate", "value": "2026-10-01", "operator": ">="}]),
    ]
    for label, items in cands:
        show(f"D filter: {label}", call(url, page(5, order={"receivedDate": "desc"}, items=items)), n=3)
    show("D advancedFilter receivedDate >= ISO", call(url, page(
        5, order={"receivedDate": "desc"}, adv=[{"filterItems": [
            {"columnName": "receivedDate", "value": "2026-10-01", "operator": ">="}]}])), n=3)

    # E. detail endpoint guesses
    rid = "131207"
    for label, u, meth, b in (
        ("GET /planningApplications/{id}", f"{BASE}/{rid}", "GET", None),
        ("GET /planningApplications/{id}/details", f"{BASE}/{rid}/details", "GET", None),
        ("GET /planningApplications/details/{id}", f"{BASE}/details/{rid}", "GET", None),
    ):
        st, d = call(u, b, meth)
        print(f"\n--- E {label} ---\n  HTTP {st}: {str(d)[:400]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
