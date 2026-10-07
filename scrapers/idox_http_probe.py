#!/usr/bin/env python3
"""
Can application pages be read WITHOUT a browser? (2026-10-07)

Sweeping ~50,000 old pending Idox applications one browser page at a time
would take ~70 hours. Application pages are normally plain server-rendered
HTML, so ordinary HTTP requests might read them in about a second each (and
put less load on the council than a full browser page, which also pulls
scripts, styles and images). Whether each portal ALLOWS that is unknown —
some reject non-browser clients — so this tests it on the same 10 councils
as the Decided survey, using the application links we already store.

For each council it takes up to 3 old pending applications from the database
and fetches each application's page two ways: a bare request, and a request
after first visiting the portal's search page to pick up its session cookie.
It records status, speed and whether the Decision row is present. It reads
from the database and writes NOTHING.
Needs idox_scraper.py, idox_councils.py, medway_decisions.py and
idox_decided_survey.py alongside it.
"""
import asyncio
import time
from datetime import date, timedelta

import httpx

import idox_scraper as scraper
import medway_decisions as md
from idox_decided_survey import SAMPLE, entry_for

PER_COUNCIL = 3
MIN_AGE_DAYS = 60                      # old enough that most are decided
PAUSE = 1.5
UA = scraper.CONTEXT_OPTIONS["user_agent"]
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
           "Accept-Language": "en-GB,en;q=0.9"}


def classify(status, html: str, error=None) -> str:
    """What happened with one request."""
    if error:
        return "NETWORK_ERROR"
    t = (html or "").lower()
    if status in (401, 403) or "just a moment" in t or "attention required" in t:
        return "BLOCKED"
    if status == 429 or "too many requests" in t:
        return "RATE_LIMITED"
    if status and status >= 400:
        return "HTTP_ERROR"
    fields = md.detail_fields(html)
    if "reference" in fields or "proposal" in fields:
        return "OK_WITH_DECISION" if "decision" in fields else "OK_NO_DECISION_ROW"
    return "NOT_AN_APPLICATION_PAGE"


async def fetch(client, url):
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        return r.status_code, r.text, time.monotonic() - t0, None
    except Exception as e:
        return None, "", time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:80]}"


async def probe_council(name):
    got = entry_for(name)
    rows = await scraper._supa_get("councils", select="id", name=f"eq.{name}")
    if got is None or len(rows) != 1:
        return {"name": name, "error": "council not found", "results": []}
    base, _ = got
    cid = rows[0]["id"]
    cutoff = (date.today() - timedelta(days=MIN_AGE_DAYS)).isoformat()
    apps = await scraper._supa_get(
        "planning_applications", select="reference,council_url", council_id=f"eq.{cid}",
        status="eq.pending", council_url="not.is.null", submitted_date=f"lt.{cutoff}",
        order="submitted_date.desc", limit=str(PER_COUNCIL))
    results = []
    for app in apps:
        url = app["council_url"]
        for mode in ("bare", "with session"):
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as client:
                if mode == "with session":
                    await fetch(client, f"{base}/search.do?action=simple&searchType=Application")
                    await asyncio.sleep(PAUSE)
                status, html, secs, err = await fetch(client, url)
            fields = md.detail_fields(html) if not err else {}
            results.append({"ref": app["reference"], "mode": mode, "status": status, "secs": secs,
                            "bytes": len(html), "category": classify(status, html, err),
                            "decision": fields.get("decision"), "error": err})
            await asyncio.sleep(PAUSE)
    return {"name": name, "error": None, "results": results, "n_apps": len(apps)}


async def main():
    if not scraper.SUPABASE_URL or not scraper.SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        return
    print(f"Testing plain HTTP on {len(SAMPLE)} councils, up to {PER_COUNCIL} applications each\n")
    all_results = []
    for name in SAMPLE:
        out = await probe_council(name)
        print(f"{'=' * 60}\n{name}")
        if out["error"]:
            print(f"  {out['error']}")
            continue
        if not out["results"]:
            print("  no old pending applications with a stored link to test")
        for r in out["results"]:
            print(f"  {r['ref']:18} {r['mode']:13} HTTP {r['status']}  {r['secs']:.1f}s  "
                  f"{r['bytes']:>7} bytes  {r['category']}  decision={r['decision']!r}"
                  + (f"  {r['error']}" if r["error"] else ""))
        all_results.extend((name, r) for r in out["results"])

    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for mode in ("bare", "with session"):
        rs = [r for _, r in all_results if r["mode"] == mode]
        ok = [r for r in rs if r["category"].startswith("OK")]
        if rs:
            avg = sum(r["secs"] for r in ok) / len(ok) if ok else 0
            print(f"{mode:13}: {len(ok)} of {len(rs)} requests returned a usable application page; "
                  f"average {avg:.1f}s per successful page")
    by_council = {}
    for name, r in all_results:
        by_council.setdefault(name, []).append(r["category"])
    print("\nper council (bare + with-session categories):")
    for name, cats in by_council.items():
        print(f"   {name:42} {sorted(set(cats))}")
    print("\nProbe complete (nothing was written to the database).")


if __name__ == "__main__":
    asyncio.run(main())
