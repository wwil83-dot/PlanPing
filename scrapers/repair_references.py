#!/usr/bin/env python3
"""
Repair applications whose REFERENCE is really the description (2026-10-09).

Some Idox rows were saved with the application description in the reference
column: ~4,000 rows in 26 councils. On most councils that stopped on about 16
July; on the Babergh / Mid Suffolk portal it was still happening until
reference_fix_hook.py (the newer Idox template labels the number
"Application. No:" in its own <strong> tag, which the parser couldn't read).

For each such row this opens the application's own page (the link is stored on
the row), reads the real reference, and then either
  repairs   the row: sets its reference to the real one, or
  deletes   the row, if the council ALREADY holds the real reference (a proper
            copy exists, so this one is just the junk twin).
Rows whose page can't be read, or shows no reference, are left untouched.

It works by DATABASE ID and checks the council as well, so a row is only ever
changed if it is still the one that was planned. Plain HTTP, no browser; one
request at a time per portal host, PAUSE_SECONDS apart.

DRY RUN BY DEFAULT: prints what it would do. Set APPLY=1 to write.
Env: APPLY, CONCURRENCY (hosts at once, default 5), PER_HOST_MAX (default 2000),
MAX_MINUTES (default 120), PAUSE_SECONDS (default 1.5), INSECURE_OK (comma-
separated council names whose servers send an incomplete certificate chain).
Needs idox_scraper.py and medway_decisions.py alongside it.
"""
import asyncio
import os
import re
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import urlparse

import httpx

import idox_scraper as scraper
import medway_decisions as md

APPLY = os.environ.get("APPLY", "0") == "1"
CONCURRENCY = int(os.environ.get("CONCURRENCY", "5"))
PER_HOST_MAX = int(os.environ.get("PER_HOST_MAX", "2000"))
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "120"))
PAUSE = float(os.environ.get("PAUSE_SECONDS", "1.5"))
INSECURE_OK = {s.strip() for s in os.environ.get("INSECURE_OK", "").split(",") if s.strip()}
HEADERS = {"User-Agent": scraper.CONTEXT_OPTIONS["user_agent"], "Accept": "text/html,application/xhtml+xml",
           "Accept-Language": "en-GB,en;q=0.9"}
REF_LABELS = ("reference", "application no", "application. no", "application number", "ref. no", "ref no",
              "application reference")


def looks_bad(ref) -> bool:
    ref = ref or ""
    return len(ref) > 40 or not any(ch.isdigit() for ch in ref)


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


async def scan_rows():
    """Every Idox row, council by council. A single query over the whole table timed out on the
    database; one council at a time uses the council_id index and is quick."""
    councils = await get_retry("councils", select="id,name", order="id.asc", limit="1000")
    rows = []
    for i, c in enumerate(councils, 1):
        offset = 0
        while True:
            chunk = await get_retry(
                "planning_applications", select="id,council_id,reference,council_url",
                council_id=f"eq.{c['id']}", source="eq.idox_scraper", order="id.asc",
                limit="1000", offset=str(offset))
            rows.extend(chunk)
            if len(chunk) < 1000:
                break
            offset += 1000
        if i % 50 == 0:
            print(f"   scanned {i}/{len(councils)} councils, {len(rows):,} Idox rows so far")
    return rows, {c["id"]: c["name"] for c in councils}


async def _write(sb, method, row_id, council_id, body=None):
    """Change ONE row by id (and only if it still sits under the planned council).
    Returns 'ok', 'conflict' (the new reference already exists) or 'failed'."""
    r = await sb.request(method, f"{scraper.SUPABASE_URL}/rest/v1/planning_applications",
                         params={"id": f"eq.{row_id}", "council_id": f"eq.{council_id}", "select": "id"},
                         json=body, headers={**scraper._h(), "Prefer": "return=representation"})
    if r.status_code == 409:
        return "conflict"
    if r.status_code in (200, 204) and (r.status_code == 204 or len(r.json()) == 1):
        return "ok"
    return "failed"


async def repair_host(sem, sb, host, rows, held, names, deadline, stats, examples):
    s = stats[host] = Counter()
    notes = stats.setdefault("_notes", {})
    async with sem:
        insecure = any(names.get(r["council_id"]) in INSECURE_OK for r in rows)
        async with make_client(verify=not insecure) as client:
            consecutive_errors = 0
            for row in rows[:PER_HOST_MAX]:
                if time.monotonic() > deadline:
                    notes[host] = "stopped: time budget"
                    break
                status, html, err = await fetch_page(client, row["council_url"])
                cat = classify_page(status, html, err)
                if cat in ("BLOCKED", "RATE_LIMITED"):
                    notes[host] = f"stopped: {cat} (HTTP {status})"
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
                    await asyncio.sleep(PAUSE)
                    continue
                consecutive_errors = 0
                s["read"] += 1
                if cat != "OK":
                    s["odd_page"] += 1
                    await asyncio.sleep(PAUSE)
                    continue
                real = real_reference(md.detail_fields(html))
                if not real:
                    s["no_reference_on_page"] += 1
                    await asyncio.sleep(PAUSE)
                    continue
                cid, key = row["council_id"], (row["council_id"], real)
                action = "delete (a proper copy exists)" if key in held else "repair"
                ex = examples[names.get(cid, cid)]
                if len(ex) < 3:
                    ex.append((row["reference"][:55], real, action))
                if APPLY:
                    if action == "repair":
                        result = await _write(sb, "PATCH", row["id"], cid, {"reference": real})
                        if result == "conflict":                  # a twin appeared since the scan
                            action, result = "delete (a proper copy exists)", await _write(sb, "DELETE", row["id"], cid)
                        elif result == "ok":
                            held.add(key)
                    else:
                        result = await _write(sb, "DELETE", row["id"], cid)
                    if result != "ok":
                        s["write_failed"] += 1
                        await asyncio.sleep(PAUSE)
                        continue
                else:
                    if action == "repair":
                        held.add(key)                             # a second junk row for it would then be a twin
                s["repaired" if action == "repair" else "deleted_twin"] += 1
                await asyncio.sleep(PAUSE)


async def main():
    if not scraper.SUPABASE_URL or not scraper.SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    print(f"{'APPLY — changes WILL be made' if APPLY else 'DRY RUN — nothing will be changed'}   "
          f"hosts at once={CONCURRENCY}  pause={PAUSE}s  budget={MAX_MINUTES} min\n")
    allrows, all_names = await scan_rows()
    held = {(r["council_id"], r["reference"]) for r in allrows if not looks_bad(r["reference"])}
    junk = [r for r in allrows if looks_bad(r["reference"])]
    no_link = [r for r in junk if not r.get("council_url")]
    junk = [r for r in junk if r.get("council_url")]
    names = all_names
    by_host = defaultdict(list)
    for r in junk:
        by_host[urlparse(r["council_url"]).netloc].append(r)
    print(f"{len(allrows):,} Idox rows scanned: {len(junk) + len(no_link):,} have a reference that is really a description "
          f"({len(no_link)} have no page link, so can't be repaired); {len(junk):,} to read, across {len(by_host)} portals\n")
    per_council = Counter(names.get(r["council_id"], r["council_id"]) for r in junk)
    for n, c in per_council.most_common(12):
        print(f"   {n:44} {c}")
    print()

    stats, examples = {}, defaultdict(list)
    deadline = time.monotonic() + MAX_MINUTES * 60
    sem = asyncio.Semaphore(CONCURRENCY)
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=30) as sb:
        await asyncio.gather(*[repair_host(sem, sb, h, rows, held, names, deadline, stats, examples)
                               for h, rows in sorted(by_host.items(), key=lambda kv: -len(kv[1]))])
    notes = stats.pop("_notes", {})
    print(f"{'=' * 60}\nSUMMARY ({'applied' if APPLY else 'dry run'}, {(time.monotonic() - t0) / 60:.1f} min)\n{'=' * 60}")
    print(f"{'portal':46} {'read':>5} {'repair':>6} {'twin':>5} {'noref':>5} {'err':>4}  note")
    total = Counter()
    for host in sorted(stats, key=lambda h: -stats[h]["read"]):
        s = stats[host]; total.update(s)
        print(f"{host[:46]:46} {s['read']:5} {s['repaired']:6} {s['deleted_twin']:5} {s['no_reference_on_page']:5} "
              f"{s['errors']:4}  {notes.get(host, '')}")
    print(f"\nTOTAL read {total['read']}: {'repaired' if APPLY else 'would repair'} {total['repaired']}, "
          f"{'deleted as twins' if APPLY else 'would delete as twins'} {total['deleted_twin']}, "
          f"no reference on the page {total['no_reference_on_page']}, odd pages {total['odd_page']}, "
          f"write failures {total['write_failed']}")
    print("\nexamples (junk text -> real reference):")
    for name, exs in sorted(examples.items(), key=lambda kv: str(kv[0])):
        for junk_text, real, action in exs[:2]:
            print(f"   {str(name)[:30]:30} {junk_text!r:60} -> {real}   [{action}]")
    if not APPLY:
        print("\nDry run finished. Re-run with APPLY=1 to make these changes.")


if __name__ == "__main__":
    asyncio.run(main())
