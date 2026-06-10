#!/usr/bin/env python3
"""
PlanPing — geocoder.
Finds applications with postcodes but no coordinates and geocodes them.
Run after bulk imports to add lat/lng without slowing down the main scraper.
"""
import asyncio
import os
import asyncpg
import httpx

DATABASE_URL = os.environ["DATABASE_URL"]
BATCH = 100


async def main():
    pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=1, max_size=3,
        statement_cache_size=0, ssl="require"
    )

    async with pool.acquire() as db:
        # Find applications needing geocoding
        rows = await db.fetch("""
            SELECT id, postcode FROM planning_applications
            WHERE lat IS NULL AND postcode IS NOT NULL
            LIMIT 10000
        """)

    print(f"Found {len(rows)} applications needing geocoding")

    postcodes = list({r["postcode"].strip().upper().replace(" ","")
                      for r in rows if r["postcode"]})
    print(f"  {len(postcodes)} unique postcodes to geocode")

    coords = {}
    for i in range(0, len(postcodes), BATCH):
        chunk = postcodes[i:i+BATCH]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": chunk}
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        coords[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"]
                        )
        except Exception as e:
            print(f"  Geocode error batch {i}: {e}")
        await asyncio.sleep(0.2)

    print(f"  Geocoded {len(coords)} postcodes")

    # Update database
    updated = 0
    async with pool.acquire() as db:
        for row in rows:
            pc = (row["postcode"] or "").strip().upper().replace(" ","")
            coord = coords.get(pc)
            if coord:
                await db.execute("""
                    UPDATE planning_applications
                    SET lat=$1, lng=$2, updated_at=NOW()
                    WHERE id=$3
                """, coord[0], coord[1], row["id"])
                updated += 1

    print(f"  Updated {updated} applications with coordinates")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
