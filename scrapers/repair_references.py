#!/usr/bin/env python3
"""
Repair applications whose REFERENCE is wrong (2026-10-09).

TWO different bugs left ~2,800 Idox rows with a bad reference:

 1. "reference·Received: Wed 01 Jul 2026 ·Validated: ..." (Northumberland, Fife, Isle
    of Wight, West Oxfordshire, Cheltenham, Thurrock, Luton, ... about 1,500 rows).
    The real reference is already in the row: it is the text before the first "·".
    Those are repaired LOCALLY — no web request at all, so certificate problems,
    blocks and rate limits don't matter.
 2. The application DESCRIPTION saved as the reference (Babergh / Mid Suffolk, about
    1,280 rows; fixed going forward by reference_fix_hook.py). The real reference is
    only on the application's own page, so those pages are read, one request at a time
    per portal, with a longer pause for portals that rate-limit (SLOW_HOSTS), backing
    off and retrying when a portal answers 429.

For each row the outcome is either
  repair   set its reference to the real one, or
  delete   the row, if the council ALREADY holds the real reference (a proper copy
           exists, so this one is just the junk twin).
Writes go by DATABASE ID and only if the row still sits under the planned council.

DRY RUN BY DEFAULT: prints what it would do. Set APPLY=1 to write.
Env: APPLY, CONCURRENCY (portals at once, default 5), PER_HOST_MAX (default 2000),
MAX_MINUTES (default 240), PAUSE_SECONDS (default 1.5), SLOW_HOSTS
("host=seconds,host=seconds", default planning.baberghmidsuffolk.gov.uk=8),
BACKOFF_SECONDS (wait after a 429 before one retry, default 120), INSECURE_OK
(council names whose servers send an incomplete certificate chain).
Needs idox_scraper.py and medway_decisions.py alongside it.
"""
import asyncio
import os
import re
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import parse_qs, urlparse

import httpx

import idox_scraper as scraper
import medway_decisions as md

APPLY = os.environ.get("APPLY", "0") == "1"
CONCURRENCY = int(os.environ.get("CONCURRENCY", "5"))
PER_HOST_MAX = int(os.environ.get("PER_HOST_MAX", "2000"))
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "240"))
PAUSE = float(os.environ.get("PAUSE_SECONDS", "1.5"))
BACKOFF = float(os.environ.get("BACKOFF_SECONDS", "120"))
INSECURE_OK = {s.strip() for s in os.environ.get("INSECURE_OK", "").split(",") if s.strip()}
SLOW_HOSTS: dict = {}
for _item in os.environ.get("SLOW_HOSTS", "planning.baberghmidsuffolk.gov.uk=8").split(","):
    _host, _, _secs = _item.partition("=")
    if _host.strip() and _secs.strip():
        SLOW_HOSTS[_host.strip()] = float(_secs)
MAX_BACKOFFS_PER_HOST = 3
HEADERS = {"User-Agent": scraper.CONTEXT_OPTIONS["user_agent"], "Accept": "text/html,application/xhtml+xml",
           "Accept-Language": "en-GB,en;q=0.9"}
REF_LABELS = ("reference", "application no", "application. no", "application number", "ref. no", "ref no",
              "application reference")
LABEL_AFTER_DOT = re.compile(r"\s*(received|validated|status|decision|decided|registered|date)\b", re.I)


def looks_bad(ref) -> bool:
    ref = ref or ""
    return len(ref) > 40 or not any(ch.isdigit() for ch in ref)


def derive_reference(text):
    """'26/00981/TPO·Received: Wed 01 Jul 2026 ...' -> '26/00981/TPO'. Only when what follows the first
    '·' is a field label (Received, Validated, ...) and what precedes it is a plausible reference.
    A description that merely contains a '·' is NOT touched."""
    if not text or "·" not in text:
        return None
    head, tail = text.split("·", 1)
    head = head.strip()
    if head and not looks_bad(head) and LABEL_AFTER_DOT.match(tail):
        return head
    return None


def real_reference(fields: dict):
    """The application's own reference from its page's summary table."""
    for label in REF_LABELS:
        v = (fields.get(label) or "").strip()
        if v and not looks_bad(v):
            return v
    return None


def classify_page(status, html, error=None):
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
    return "OK" if ("reference" in fields or "proposal" in fields) else "NOT_AN_APPLICATION_PAGE"


def make_client(verify):
    return httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20, verify=verify)


async def fetch_page(client, url):
    try:
        r = await client.get(url)
        return r.status_code, r.text, None
    except Exception as e:
        return None, "", f"{type(e).__name__}: {str(e)[:120]}"


async def get_retry(table, **params):
    """scraper._supa_get with retries, and the database's own error message when it fails."""
    for attempt in range(4):
        try:
            return await scraper._supa_get(table, **params)
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300].replace("\n", " ")
            print(f"   ⚠ database returned HTTP {e.response.status_code} for {table}: {body}")
            if e.response.status_code < 500 or attempt == 3:
                raise
            await asyncio.sleep(2 * (attempt + 1))


async def scan_rows():
    """Every Idox row, read by walking the table's primary key 1,000 rows at a time (id > the last id
    seen). Filtering by council_id/source as well made the database walk the whole table for councils
    with few matching rows and hit its statement timeout (error 57014)."""
    rows, last_id, pages = [], 0, 0
    while pages < 3000:
        chunk = await get_retry("planning_applications", select="id,council_id,reference,council_url,source",
                                id=f"gt.{last_id}", order="id.asc", limit="1000")
        if not chunk:
            break
        pages += 1
        last_id = chunk[-1]["id"]
        rows.extend({k: r[k] for k in ("id", "council_id", "reference", "council_url")}
                    for r in chunk if r.get("source") == "idox_scraper")
        if pages % 50 == 0:
            print(f"   read {pages * 1000:,} rows of the table (up to id {last_id:,}); {len(rows):,} are Idox")
        if len(chunk) < 1000:
            break
    councils = await get_retry("councils", select="id,name", order="id.asc", limit="1000")
    return rows, {c["id"]: c["name"] for c in councils}


async def _write(sb, method, row_id, council_id, body=None):
    """Change ONE row by id (only if it still sits under the planned council).
    Returns 'ok', 'conflict' (the new reference already exists) or 'failed'."""
    r = await sb.request(method, f"{scraper.SUPABASE_URL}/rest/v1/planning_applications",
                         params={"id": f"eq.{row_id}", "council_id": f"eq.{council_id}", "select": "id"},
                         json=body, headers={**scraper._h(), "Prefer": "return=representation"})
    if r.status_code == 409:
        return "conflict"
    if r.status_code in (200, 204) and (r.status_code == 204 or len(r.json()) == 1):
        return "ok"
    return "failed"


async def apply_real_reference(sb, row, real, held):
    """Repair the row to `real`, or delete it if the council already holds `real`.
    Returns 'repair', 'delete' or 'failed'."""
    cid, key = row["council_id"], (row["council_id"], real)
    action = "delete" if key in held else "repair"
    if APPLY:
        if action == "repair":
            result = await _write(sb, "PATCH", row["id"], cid, {"reference": real})
            if result == "conflict":                              # a twin appeared since the scan
                action, result = "delete", await _write(sb, "DELETE", row["id"], cid)
            elif result == "ok":
                held.add(key)
        else:
            result = await _write(sb, "DELETE", row["id"], cid)
        return action if result == "ok" else "failed"
    if action == "repair":
        held.add(key)                                              # a second junk row for it would then be a twin
    return action


def key_of(url):
    """The portal's own id for an application (keyVal=...) from its page link."""
    try:
        v = parse_qs(urlparse(url or "").query).get("keyVal")
        return v[0] if v else None
    except Exception:
        return None


def split_twins(allrows, remote):
    """Split description-reference rows into (twins, rest). A twin is a bad row whose application (same
    council, same portal keyVal) is ALSO held by a row with a proper reference, e.g. one saved by the
    nightly run after the reference fix. It needs no page read: the good row already says what it is."""
    good = {(r["council_id"], key_of(r.get("council_url"))) for r in allrows
            if not looks_bad(r["reference"]) and key_of(r.get("council_url"))}
    twins, rest = [], []
    for r in remote:
        k = key_of(r.get("council_url"))
        (twins if k and (r["council_id"], k) in good else rest).append(r)
    return twins, rest


async def delete_twins(sb, twins, names, stats, examples):
    for row in twins:
        council = names.get(row["council_id"], row["council_id"])
        result = "ok" if not APPLY else await _write(sb, "DELETE", row["id"], row["council_id"])
        stats[council]["deleted_twin" if result == "ok" else "write_failed"] += 1
        if len(examples[council]) < 2 and result == "ok":
            examples[council].append((row["reference"][:40].replace("\n", " "), "(already held under its real reference)", "delete"))


async def repair_local(sb, rows, held, names, stats, examples):
    """Rows whose reference is 'real·Received: ...': the real reference is already in the row."""
    for row in rows:
        real = derive_reference(row["reference"])
        council = names.get(row["council_id"], row["council_id"])
        result = await apply_real_reference(sb, row, real, held)
        stats[council]["repaired" if result == "repair" else "deleted_twin" if result == "delete" else "write_failed"] += 1
        if len(examples[council]) < 2 and result != "failed":
            examples[council].append((row["reference"][:40].replace("\n", " "), real, result))


async def repair_host(sem, sb, host, rows, held, names, deadline, stats, notes, examples):
    s = stats[host] = Counter()
    pause = max(PAUSE, SLOW_HOSTS.get(host, 0))
    async with sem:
        insecure = any(names.get(r["council_id"]) in INSECURE_OK for r in rows)
        async with make_client(verify=not insecure) as client:
            consecutive_errors = backoffs = 0
            for row in rows[:PER_HOST_MAX]:
                if time.monotonic() > deadline:
                    notes[host] = "stopped: time budget"
                    break
                status, html, err = await fetch_page(client, row["council_url"])
                cat = classify_page(status, html, err)
                if cat == "RATE_LIMITED" and backoffs < MAX_BACKOFFS_PER_HOST:
                    backoffs += 1
                    await asyncio.sleep(BACKOFF)                   # wait, then try this row once more
                    status, html, err = await fetch_page(client, row["council_url"])
                    cat = classify_page(status, html, err)
                if cat in ("BLOCKED", "RATE_LIMITED"):
                    notes[host] = f"stopped: {cat} (HTTP {status}) after {backoffs} back-off(s)"
                    break
                if cat == "NETWORK_ERROR":
                    s["errors"] += 1
                    consecutive_errors += 1
                    if "CERTIFICATE_VERIFY_FAILED" in (err or ""):
                        notes[host] = "stopped: certificate chain incomplete — name the council in INSECURE_OK to read it"
                        break
                    if consecutive_errors >= 3:
                        notes[host] = f"stopped: 3 network errors in a row ({err})"
                        break
                    await asyncio.sleep(pause)
                    continue
                consecutive_errors = 0
                s["read"] += 1
                if cat != "OK":
                    s["odd_page"] += 1
                    await asyncio.sleep(pause)
                    continue
                real = real_reference(md.detail_fields(html))
                if not real:
                    s["no_reference_on_page"] += 1
                    await asyncio.sleep(pause)
                    continue
                result = await apply_real_reference(sb, row, real, held)
                council = names.get(row["council_id"], row["council_id"])
                if len(examples[council]) < 3 and result != "failed":
                    examples[council].append((row["reference"][:40].replace("\n", " "), real, result))
                s["repaired" if result == "repair" else "deleted_twin" if result == "delete" else "write_failed"] += 1
                await asyncio.sleep(pause)


async def main():
    if not scraper.SUPABASE_URL or not scraper.SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    print(f"{'APPLY — changes WILL be made' if APPLY else 'DRY RUN — nothing will be changed'}   "
          f"portals at once={CONCURRENCY}  pause={PAUSE}s (slow portals: {SLOW_HOSTS or 'none'})  budget={MAX_MINUTES} min\n")
    allrows, names = await scan_rows()
    held = {(r["council_id"], r["reference"]) for r in allrows if not looks_bad(r["reference"])}
    junk = [r for r in allrows if looks_bad(r["reference"])]
    local = [r for r in junk if derive_reference(r["reference"])]
    remote = [r for r in junk if not derive_reference(r["reference"]) and r.get("council_url")]
    no_link = [r for r in junk if not derive_reference(r["reference"]) and not r.get("council_url")]
    twins, remote = split_twins(allrows, remote)
    by_host = defaultdict(list)
    for r in remote:
        by_host[urlparse(r["council_url"]).netloc].append(r)
    print(f"{len(allrows):,} Idox rows scanned: {len(junk):,} have a bad reference\n"
          f"   {len(local):,} are 'reference·Received: …' — repaired from the row's own text, no web request\n"
          f"   {len(twins):,} are descriptions whose application is already saved under its real reference — "
          f"duplicates, removed without a web request\n"
          f"   {len(remote):,} are descriptions — need their page read, across {len(by_host)} portals\n"
          f"   {len(no_link):,} can't be repaired (no page link)\n")
    for label, rows in (("from the text", local), ("duplicates", twins), ("from the page", remote)):
        per = Counter(names.get(r["council_id"], r["council_id"]) for r in rows)
        print(f"   {label}: " + ", ".join(f"{n} {c}" for n, c in per.most_common(8)))
    print()

    local_stats, examples, notes, stats = defaultdict(Counter), defaultdict(list), {}, {}
    t0 = time.monotonic()
    deadline = t0 + MAX_MINUTES * 60
    async with httpx.AsyncClient(timeout=30) as sb:
        await repair_local(sb, local, held, names, local_stats, examples)
        await delete_twins(sb, twins, names, local_stats, examples)
        sem = asyncio.Semaphore(CONCURRENCY)
        await asyncio.gather(*[repair_host(sem, sb, h, rows, held, names, deadline, stats, notes, examples)
                               for h, rows in sorted(by_host.items(), key=lambda kv: -len(kv[1]))])

    verb = ("repaired", "deleted as twins") if APPLY else ("would repair", "would delete as twins")
    print(f"{'=' * 60}\nSUMMARY ({'applied' if APPLY else 'dry run'}, {(time.monotonic() - t0) / 60:.1f} min)\n{'=' * 60}")
    print("\nA. fixed from data already in the database (no web requests):")
    print(f"   {'council':44} {'repair':>7} {'twin':>6}")
    lt = Counter()
    for council in sorted(local_stats, key=lambda c: -sum(local_stats[c].values())):
        c = local_stats[council]; lt.update(c)
        print(f"   {str(council)[:44]:44} {c['repaired']:7} {c['deleted_twin']:6}" + (f"   ⚠ {c['write_failed']} write failures" if c["write_failed"] else ""))
    print(f"   TOTAL: {verb[0]} {lt['repaired']}, {verb[1]} {lt['deleted_twin']}")
    print("\nB. repaired by reading the application's own page:")
    print(f"   {'portal':46} {'read':>5} {'repair':>6} {'twin':>5} {'noref':>5} {'odd':>4} {'err':>4}  note")
    rt = Counter()
    for host in sorted(stats, key=lambda h: -stats[h]["read"]):
        s = stats[host]; rt.update(s)
        print(f"   {host[:46]:46} {s['read']:5} {s['repaired']:6} {s['deleted_twin']:5} {s['no_reference_on_page']:5} "
              f"{s['odd_page']:4} {s['errors']:4}  {notes.get(host, '')}")
    print(f"   TOTAL read {rt['read']}: {verb[0]} {rt['repaired']}, {verb[1]} {rt['deleted_twin']}, "
          f"no reference on page {rt['no_reference_on_page']}, odd pages {rt['odd_page']}, write failures {rt['write_failed']}")
    left = sum(len(v) for v in by_host.values()) - rt["read"]
    if left > 0:
        print(f"   {left} description rows were not read this run — re-run to continue (it only acts on what is still wrong).")
    print("\nexamples (bad reference -> real reference):")
    for council, exs in sorted(examples.items(), key=lambda kv: str(kv[0])):
        for text, real, result in exs[:2]:
            print(f"   {str(council)[:30]:30} {text!r:44} -> {real}   [{result}]")
    if not APPLY:
        print("\nDry run finished. Re-run with APPLY=1 to make these changes.")


if __name__ == "__main__":
    asyncio.run(main())
