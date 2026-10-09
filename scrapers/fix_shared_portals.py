#!/usr/bin/env python3
"""
Clean up applications filed under the wrong council on shared Idox portals
(2026-10-08). See shared_portals.py for the cause.

For every shared portal it reads the applications held by its councils, looks
each postcode up on postcodes.io to find the council it really belongs to, and
decides for each row:
  keep        the postcode is in the district of the council holding it
  delete      it belongs to another council, which ALREADY holds the same
              reference — this is just the wrong copy
  move        it belongs to another council that does NOT hold it yet (e.g.
              Mid Suffolk applications filed under Babergh) — re-filed there
  leave       no usable postcode, so nobody can say whose it is: left alone and
              counted (the nightly filter stops any more being added)
Rows held by a joint "council" (Bromsgrove and Redditch) are duplicates of rows
the two real councils hold, and are deleted when the reference exists there.

DRY RUN BY DEFAULT: it prints the plan and changes nothing. Set APPLY=1 to
execute. It needs the Mid Suffolk row to exist in the councils table first.
Env: APPLY (default 0). Needs idox_scraper.py (for the database helpers) and
shared_portals.py alongside it.
"""
import asyncio
import os
import sys
from collections import Counter, defaultdict

import httpx

import idox_scraper as scraper
from shared_portals import SHARED_PORTALS, JOINT_COUNCILS, lookup_districts, norm_postcode

APPLY = os.environ.get("APPLY", "0") == "1"


def plan_portal(holders: dict, rows: list, districts: dict, mapping: dict, joint_ids: set):
    """holders: council_id -> name; rows: [{council_id, reference, postcode}].
    mapping: district -> council_id. Returns (actions, stats)."""
    held = {(r["council_id"], r["reference"]) for r in rows}
    claimed = set()
    actions, stats = [], Counter()
    for r in rows:
        cid, ref = r["council_id"], r["reference"]
        district = districts.get(norm_postcode(r.get("postcode")))
        target = mapping.get(district) if district else None
        if cid in joint_ids:
            # a joint row duplicates the real councils' rows; delete it if one holds the reference
            if any((m, ref) in held for m in mapping.values()):
                actions.append(("delete", cid, ref, None, r.get("id"))); stats["delete (joint duplicate)"] += 1
            elif target and (target, ref) not in claimed:
                actions.append(("move", cid, ref, target, r.get("id"))); claimed.add((target, ref)); stats["move"] += 1
            else:
                stats["leave"] += 1
            continue
        if target is None:
            stats["leave (no usable postcode / district not mapped)"] += 1
        elif target == cid:
            stats["keep"] += 1
        elif (target, ref) in held or (target, ref) in claimed:
            actions.append(("delete", cid, ref, None, r.get("id"))); stats["delete (wrong copy)"] += 1
        else:
            actions.append(("move", cid, ref, target, r.get("id"))); claimed.add((target, ref)); stats["move"] += 1
    return actions, stats


async def read_rows(council_ids):
    rows, size = [], 1000
    for page in range(60):
        chunk = await scraper._supa_get(
            "planning_applications", select="id,council_id,reference,postcode",
            council_id=f"in.({','.join(str(c) for c in council_ids)})", order="id.asc",
            limit=str(size), offset=str(page * size))
        rows.extend(chunk)
        if len(chunk) < size:
            break
    return rows


CHUNK = 100


async def _write(client, method, council_id, items, json_body=None, what="changed"):
    """DELETE/PATCH rows by their database id, in chunks. items = [(row_id, reference)].
    Returns how many rows the database says it changed and names any it did NOT change.
    The council_id filter is a safety net: a row is only touched if it still sits under
    the council we planned it for."""
    done, missing = 0, []
    for i in range(0, len(items), CHUNK):
        part = items[i:i + CHUNK]
        ids = [rid for rid, _ in part]
        r = await client.request(method, f"{scraper.SUPABASE_URL}/rest/v1/planning_applications",
                                 params={"council_id": f"eq.{council_id}", "id": "in.(" + ",".join(str(x) for x in ids) + ")",
                                         "select": "id"},
                                 json=json_body, headers={**scraper._h(), "Prefer": "return=representation"})
        if r.status_code not in (200, 204):
            raise RuntimeError(f"{method} failed: HTTP {r.status_code} {r.text[:200]}")
        got = {row["id"] for row in r.json()} if r.status_code == 200 else set()
        done += len(got)
        missing += [(rid, ref) for rid, ref in part if rid not in got]
    if missing:
        print(f"      ⚠ {len(missing)} of {len(items)} NOT {what}: " + "; ".join(f"id {rid} {str(ref)[:50]!r}" for rid, ref in missing[:6]))
    return done


async def delete_rows(client, council_id, items):
    return await _write(client, "DELETE", council_id, items, what="deleted")


async def move_rows(client, from_id, to_id, items):
    return await _write(client, "PATCH", from_id, items, json_body={"council_id": to_id}, what="moved")


async def main():
    if not scraper.SUPABASE_URL or not scraper.SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    print(f"{'APPLY — changes WILL be made' if APPLY else 'DRY RUN — nothing will be changed'}\n")
    all_names = {n for m in SHARED_PORTALS.values() for n in m.values()} | {n for js in JOINT_COUNCILS.values() for n in js}
    rows_c = await scraper._supa_get("councils", select="id,name", name=f"in.({','.join(chr(34) + n + chr(34) for n in sorted(all_names))})")
    ids = {r["name"]: r["id"] for r in rows_c}
    missing = sorted(all_names - set(ids))
    if missing:
        print(f"⚠ not found in the councils table: {missing}")
    plans = []
    async with httpx.AsyncClient(timeout=60) as sb:
        for portal, mapping_names in SHARED_PORTALS.items():
            joint = JOINT_COUNCILS.get(portal, [])
            names = list(mapping_names.values()) + joint
            if any(n not in ids for n in mapping_names.values()):
                print(f"\n{portal}\n   skipped: {[n for n in mapping_names.values() if n not in ids]} has no councils row yet")
                continue
            holders = {ids[n]: n for n in names if n in ids}
            rows = await read_rows(list(holders))
            districts = await lookup_districts([r["postcode"] for r in rows])
            mapping = {d: ids[n] for d, n in mapping_names.items()}
            joint_ids = {ids[n] for n in joint if n in ids}
            actions, stats = plan_portal(holders, rows, districts, mapping, joint_ids)
            seen = Counter(districts.get(norm_postcode(r["postcode"])) or "(none)" for r in rows)
            print(f"\n{'=' * 60}\n{portal}\n{'=' * 60}")
            held_counts = Counter(r["council_id"] for r in rows)
            for cid, n in holders.items():
                print(f"   {n:44} holds {held_counts[cid]:5} rows")
            print(f"   districts the postcodes resolve to: {dict(seen.most_common(8))}")
            for k, v in stats.most_common():
                print(f"   {k:55} {v:6}")
            plans.append((portal, holders, actions, mapping, joint_ids))
        if APPLY:
            print("\nAPPLYING…")
            for portal, holders, actions, mapping, joint_ids in plans:
                by_del, by_move = defaultdict(list), defaultdict(list)
                for kind, cid, ref, target, rid in actions:
                    (by_del[cid] if kind == "delete" else by_move[(cid, target)]).append((rid, ref))
                for (frm, to), items in by_move.items():          # moves first, then deletes
                    n = await move_rows(sb, frm, to, items)
                    print(f"   moved {n} rows {holders.get(frm)} -> id {to}")
                for cid, items in by_del.items():
                    n = await delete_rows(sb, cid, items)
                    print(f"   deleted {n} rows from {holders.get(cid)}")
            print("\nVERIFYING — re-reading the database and re-planning…")
            all_clean = True
            for portal, holders, _actions, mapping, joint_ids in plans:
                rows = await read_rows(list(holders))
                districts = await lookup_districts([r["postcode"] for r in rows])
                again, stats = plan_portal(holders, rows, districts, mapping, joint_ids)
                left = Counter(a[0] for a in again)
                clean = not left
                all_clean &= clean
                held = Counter(r["council_id"] for r in rows)
                print(f"   {portal.split('//')[1].split('/')[0]:44} still to delete: {left['delete']}  still to move: {left['move']}  "
                      f"{'✓ clean' if clean else '✗ NOT CLEAN'}   now holds: "
                      + ", ".join(f"{holders[c].split(' ')[0]} {n}" for c, n in held.items()))
                for kind, cid, ref, target, rid in again[:10]:
                    print(f"        {kind:6} id {rid}  {str(ref)[:70]!r}  (held by {holders.get(cid)})")
            print("\nRESULT:", "everything that can be decided from a postcode is now in the right place."
                  if all_clean else "some rows are still wrong — see the lines above; re-run to retry.")
        else:
            print("\nDry run finished. Re-run with APPLY=1 to make these changes.")


if __name__ == "__main__":
    asyncio.run(main())
