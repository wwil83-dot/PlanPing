"""
Tag planning_applications.work_type using worktype.classify().

    python tag_work_types.py              # only rows where work_type IS NULL (nightly mode)
    python tag_work_types.py --retag      # re-classify EVERYTHING (after changing the rules)
    python tag_work_types.py --limit=50000   # stop after N rows (testing)

Gentle on Supabase: small keyset-paginated batches, one UPDATE per batch via
unnest(), short pause between batches. Safe to stop and re-run at any point.
Needs the DATABASE_URL secret (same as import_professionals).
"""
import asyncio, os, sys, time, collections
import asyncpg
from worktype import classify

BATCH = 5000
PAUSE = 0.25

async def main():
    retag = "--retag" in sys.argv
    limit = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)
    conn = await asyncpg.connect(dsn=os.environ["DATABASE_URL"], ssl="require", statement_cache_size=0)
    last_id, done, t0 = 0, 0, time.time()
    counts = collections.Counter()
    where = "id > $1" + ("" if retag else " AND work_type IS NULL")
    while True:
        rows = await conn.fetch(
            f"SELECT id, description, application_type FROM planning_applications WHERE {where} ORDER BY id LIMIT {BATCH}",
            last_id)
        if not rows:
            break
        ids, tags = [], []
        for r in rows:
            wt = classify(r["description"], r["application_type"])
            ids.append(r["id"]); tags.append(wt); counts[wt] += 1
        await conn.execute(
            "UPDATE planning_applications p SET work_type = v.wt FROM unnest($1::int[], $2::text[]) AS v(id, wt) WHERE p.id = v.id",
            ids, tags)
        last_id = rows[-1]["id"]
        done += len(rows)
        if done % 50000 < BATCH:
            print(f"[tag] {done:,} rows tagged ({done/max(1,time.time()-t0):.0f}/s), at id {last_id}", flush=True)
        if limit and done >= limit:
            break
        await asyncio.sleep(PAUSE)
    print(f"[tag] DONE: {done:,} rows in {time.time()-t0:.0f}s")
    for k, v in counts.most_common():
        print(f"  {k:16s} {v:>9,}  ({100*v/max(1,done):.1f}%)")
    await conn.close()

asyncio.run(main())
