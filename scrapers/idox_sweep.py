#!/usr/bin/env python3
"""
Idox decision sweep (2026-10-07).

PROBLEM: 91.7% of Idox applications are 'pending', including thousands that
were decided long ago. Most Idox list pages say only "Decided"; the outcome
and date live on each application's own page (confirmed on Medway, Leeds,
Durham, Lambeth, Cornwall, Stockport and Perth and Kinross: rows "Decision"
and "Decision Issued Date", dates like "Mon 28 Sep 2026").

WHAT IT DOES: takes applications that are still 'pending' with no decision
date, reads each one's own page (using the link already stored in the
database), and updates status + decision_date from it. It uses PLAIN HTTP
REQUESTS, not a browser: a test on 10 councils got usable pages from 6 at
1-2 seconds each (a browser page takes 5-6 s) and it puts less load on the
council (no scripts or images). Councils are read in parallel, but each
council only ever gets ONE request at a time, PAUSE_SECONDS apart.

SAFETY
  - UPDATES only (a PATCH on council_id + reference); it never inserts.
  - Every council has a circuit breaker: it stops that council for this run on
    a block / rate limit (403, 429, challenge page), on 3 network errors in a
    row, or if the first NO_DECISION_STOP pages show no Decision row at all
    (the council doesn't expose decisions there).
  - A council whose server has an incomplete certificate chain (Durham, Mid
    Ulster) is NOT read unless it is named in INSECURE_OK. Certificate checks
    are only switched off for the councils you list, never globally.
  - Applications younger than 21 days are never touched, so the nightly Idox
    scrape (which re-saves the last 14 days from the list pages) cannot
    overwrite what this writes.

MODES
  backfill: every eligible application aged MIN_AGE_DAYS..MAX_AGE_DAYS, newest
            first. Run it in age windows (35-90, then 91-180, then 181-400) so
            no application is read twice.
  ladder:   the nightly steady-state. An application is read only when its age
            hits a rung of 21, 35, 49, 63, 84, 112, 140, 182 days (plus
            LADDER_SLACK extra days, so one missed night doesn't skip it). No
            new database column is needed — the age IS the schedule.

Env: MODE, MIN_AGE_DAYS, MAX_AGE_DAYS, CONCURRENCY (councils at once, default
5), PER_COUNCIL_MAX (per council per run, default 400), MAX_MINUTES (default
120), PAUSE_SECONDS (default 1.5), NO_DECISION_STOP (default 20), DRY_RUN=1,
ONLY / SKIP / INSECURE_OK (comma-separated exact council names).
SKIP defaults to Rochdale Borough Council, whose stored rows look like
Oldham's applications saved under Rochdale's name.
"""
import asyncio
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

import httpx

import idox_scraper as scraper                  # production helpers only
import medway_decisions as md                   # decision parsing + the PATCH helper

MODE = os.environ.get("MODE", "backfill")
MIN_AGE = int(os.environ.get("MIN_AGE_DAYS", "35"))
MAX_AGE = int(os.environ.get("MAX_AGE_DAYS", "400"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "5"))
PER_COUNCIL_MAX = int(os.environ.get("PER_COUNCIL_MAX", "400"))
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "120"))
PAUSE = float(os.environ.get("PAUSE_SECONDS", "1.5"))
NO_DECISION_STOP = int(os.environ.get("NO_DECISION_STOP", "20"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


def _names(var, default=""):
    return {s.strip() for s in os.environ.get(var, default).split(",") if s.strip()}


ONLY = _names("ONLY")
SKIP = _names("SKIP", "Rochdale Borough Council")
INSECURE_OK = _names("INSECURE_OK")

LADDER = [21, 35, 49, 63, 84, 112, 140, 182]
LADDER_SLACK = 1
HARD_MIN_AGE = 21                       # never touch anything the nightly scrape still re-saves
HEADERS = {"User-Agent": scraper.CONTEXT_OPTIONS["user_agent"],
           "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-GB,en;q=0.9"}


# ---------------------------------------------------------------- selection

def age_days(submitted: str, today: date) -> int:
    return (today - date.fromisoformat(submitted)).days


def eligible(age: int, mode: str) -> bool:
    if age < HARD_MIN_AGE:
        return False
    if mode == "ladder":
        return any(r <= age <= r + LADDER_SLACK for r in LADDER)
    return MIN_AGE <= age <= MAX_AGE


def group_by_council(rows: list[dict], today: date, mode: str, per_council: int) -> dict:
    """council_id -> applications to read, newest first, capped."""
    groups = defaultdict(list)
    for r in rows:
        if r.get("submitted_date") and r.get("council_url") and eligible(age_days(r["submitted_date"], today), mode):
            groups[r["council_id"]].append(r)
    for cid in groups:
        groups[cid].sort(key=lambda r: r["submitted_date"], reverse=True)
        groups[cid] = groups[cid][:per_council]
    return dict(groups)


def classify_page(status, html: str, error=None) -> str:
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


# --------------------------------------------------------------------- I/O

def make_client(verify: bool):
    return httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20, verify=verify)


async def fetch_page(client, url):
    try:
        r = await client.get(url)
        return r.status_code, r.text, None
    except Exception as e:
        return None, "", f"{type(e).__name__}: {str(e)[:120]}"



async def get_retry(table, **params):
    """scraper._supa_get with retries, and the database's own error message when it fails
    (the helper alone only says '500 Internal Server Error')."""
    for attempt in range(4):
        try:
            return await scraper._supa_get(table, **params)
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300].replace("\n", " ")
            shown = {k: v for k, v in list(params.items())[:4]}
            print(f"   ⚠ database returned HTTP {e.response.status_code} for {table} {shown}: {body}")
            if e.response.status_code < 500 or attempt == 3:
                raise
            await asyncio.sleep(2 * (attempt + 1))


async def list_councils() -> dict:
    rows = await get_retry("councils", select="id,name", order="id.asc", limit="1000")
    return {r["id"]: r["name"] for r in rows}


async def fetch_candidates(today: date, council_ids) -> list[dict]:
    """Pending applications old enough to read, council by council (one query over the whole
    table is too slow for the database; per council it uses the council_id index)."""
    lo = (today - timedelta(days=MAX_AGE if MODE != "ladder" else max(LADDER) + LADDER_SLACK)).isoformat()
    hi = (today - timedelta(days=HARD_MIN_AGE)).isoformat()
    rows = []
    for n, cid in enumerate(council_ids, 1):
        offset = 0
        while True:
            chunk = await get_retry(
                "planning_applications", select="council_id,reference,council_url,submitted_date",
                council_id=f"eq.{cid}", source="eq.idox_scraper", status="eq.pending", decision_date="is.null",
                council_url="not.is.null", order="submitted_date.desc,id.asc", limit="1000", offset=str(offset),
                **{"and": f"(submitted_date.gte.{lo},submitted_date.lte.{hi})"})
            rows.extend(chunk)
            if len(chunk) < 1000:
                break
            offset += 1000
        if n % 25 == 0:
            print(f"   read candidates for {n}/{len(council_ids)} councils, {len(rows):,} so far")
    return rows


# ------------------------------------------------------------------ worker

def new_stats():
    return {"tried": 0, "approved": 0, "refused": 0, "withdrawn": 0, "dated_only": 0, "unclear": 0,
            "no_row": 0, "odd": 0, "errors": 0, "note": ""}


async def sweep_council(sem, sb, cid, name, apps, deadline, inventory, stats):
    s = stats[name] = new_stats()
    async with sem:
        insecure = name in INSECURE_OK
        async with make_client(verify=not insecure) as client:
            consecutive_errors = decisions_seen = 0
            for app in apps:
                if time.monotonic() > deadline:
                    s["note"] = "stopped: time budget"
                    break
                status, html, err = await fetch_page(client, app["council_url"])
                cat = classify_page(status, html, err)
                if cat in ("BLOCKED", "RATE_LIMITED"):
                    s["note"] = f"stopped: {cat} (HTTP {status})"
                    break
                if cat == "NETWORK_ERROR":
                    s["errors"] += 1
                    consecutive_errors += 1
                    if "CERTIFICATE_VERIFY_FAILED" in (err or ""):
                        s["note"] = "stopped: certificate chain incomplete — add to INSECURE_OK to read it"
                        break
                    if consecutive_errors >= 3:
                        s["note"] = f"stopped: 3 network errors in a row ({err})"
                        break
                    await asyncio.sleep(PAUSE)
                    continue
                consecutive_errors = 0
                s["tried"] += 1
                fields = md.detail_fields(html)
                if cat == "OK_WITH_DECISION":
                    decisions_seen += 1
                    text = fields["decision"]
                    inventory[text] += 1
                    mapped = md.outcome_from_decision(text)
                    issued = md.parse_issue_date(fields.get("decision issued date"))
                    update = {}
                    if mapped:
                        update = {"status": mapped, **({"decision_date": issued} if issued else {})}
                    elif issued:
                        update = {"decision_date": issued}
                    if update:
                        ok = DRY_RUN or await md.patch_application(sb, cid, app["reference"], update)
                        if ok:
                            s[mapped or "dated_only"] += 1
                    else:
                        s["unclear"] += 1
                elif cat == "OK_NO_DECISION_ROW":
                    s["no_row"] += 1
                else:
                    s["odd"] += 1
                if s["tried"] >= NO_DECISION_STOP and decisions_seen == 0:
                    s["note"] = f"stopped: no Decision row on the first {s['tried']} pages — not exposed here"
                    break
                await asyncio.sleep(PAUSE)
    return s


async def main():
    if not scraper.SUPABASE_URL or not scraper.SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    today = date.today()
    print(f"MODE={MODE}  ages {MIN_AGE}-{MAX_AGE} d  councils at once={CONCURRENCY}  per council max={PER_COUNCIL_MAX}  "
          f"pause={PAUSE}s  budget={MAX_MINUTES} min  {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print(f"ONLY={sorted(ONLY) or 'all'}  SKIP={sorted(SKIP)}  INSECURE_OK={sorted(INSECURE_OK) or 'none'}\n")

    names = await list_councils()
    wanted = [cid for cid, n in names.items() if (not ONLY or n in ONLY) and n not in SKIP]
    rows = await fetch_candidates(today, wanted)
    groups = group_by_council(rows, today, MODE, PER_COUNCIL_MAX)
    plan = {cid: apps for cid, apps in groups.items() if names.get(cid)}
    total = sum(len(a) for a in plan.values())
    print(f"{len(rows):,} candidate rows fetched; {total:,} applications across {len(plan)} councils to read\n")

    deadline = time.monotonic() + MAX_MINUTES * 60
    sem = asyncio.Semaphore(CONCURRENCY)
    inventory: Counter = Counter()
    stats: dict = {}
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=30) as sb:
        order = sorted(plan, key=lambda c: -len(plan[c]))        # biggest first, so they start early
        await asyncio.gather(*[sweep_council(sem, sb, cid, names[cid], plan[cid], deadline, inventory, stats)
                               for cid in order])

    print(f"{'=' * 60}\nSUMMARY ({'dry run' if DRY_RUN else 'live'}, {(time.monotonic() - t0) / 60:.1f} min)\n{'=' * 60}")
    print(f"{'council':44} {'read':>5} {'appr':>5} {'refu':>5} {'wdrn':>5} {'date':>5} {'none':>5}  note")
    tot = Counter()
    for name in sorted(stats, key=lambda n: -stats[n]["tried"]):
        s = stats[name]
        tot.update({k: v for k, v in s.items() if isinstance(v, int)})
        print(f"{name[:44]:44} {s['tried']:5} {s['approved']:5} {s['refused']:5} {s['withdrawn']:5} "
              f"{s['dated_only']:5} {s['no_row']:5}  {s['note']}")
    print(f"\nTOTAL read {tot['tried']}: approved {tot['approved']}, refused {tot['refused']}, withdrawn {tot['withdrawn']}, "
          f"date only {tot['dated_only']}, no decision yet {tot['no_row']}, unclear {tot['unclear']}, "
          f"odd pages {tot['odd']}, errors {tot['errors']}")
    print("\n'Decision' wordings seen (and how they were read):")
    for text, n in inventory.most_common(40):
        print(f"   {text!r:46} {n:5}  ->  {md.outcome_from_decision(text) or 'LEFT ALONE (date recorded if present)'}")


if __name__ == "__main__":
    asyncio.run(main())
