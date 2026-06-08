#!/usr/bin/env python3
"""
PlanPing — main scraper runner.
Orchestrates all data sources: gov API + Idox + Northgate scrapers.
Geocodes new postcodes and upserts to database.

Run via GitHub Actions nightly, or manually:
    python scrapers/run_scrapers.py
"""
import asyncio
import os
from datetime import date, datetime

import asyncpg
import httpx

from idox import scrape_idox_weekly
from northgate import scrape_northgate_weekly
from gov_api import poll_gov_api

DATABASE_URL = os.environ["DATABASE_URL"]


async def geocode_postcode(client: httpx.AsyncClient, postcode: str):
    """Geocode a single postcode via postcodes.io."""
    if not postcode:
        return None, None
    pc = postcode.strip().upper().replace(" ", "")
    try:
        r = await client.get(f"https://api.postcodes.io/postcodes/{pc}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == 200:
                result = data["result"]
                return result["latitude"], result["longitude"]
    except Exception:
        pass
    return None, None


async def bulk_geocode(postcodes: list[str]) -> dict[str, tuple]:
    """Bulk geocode up to 100 postcodes at a time."""
    results = {}
    clean = list({p.strip().upper() for p in postcodes if p})

    for i in range(0, len(clean), 100):
        chunk = clean[i:i+100]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://api.postcodes.io/postcodes",
                    json={"postcodes": chunk}
                )
                for item in r.json().get("result", []):
                    if item and item.get("result"):
                        results[item["query"]] = (
                            item["result"]["latitude"],
                            item["result"]["longitude"]
                        )
        except Exception as e:
            print(f"  Geocode error: {e}")
        await asyncio.sleep(0.3)

    return results


async def upsert_applications(db, council_id: int, apps: list[dict], coords: dict) -> tuple[int, int]:
    """Upsert applications to DB. Returns (found, new)."""
    new_count = 0

    for app in apps:
        postcode = app.get("postcode") or ""
        lat = app.get("lat")
        lng = app.get("lng")

        # Use geocoded coords if not already set
        if not lat and postcode:
            coord = coords.get(postcode.replace(" ", "").upper())
            if coord:
                lat, lng = coord

        # Check if already exists
        existing = await db.fetchval(
            "SELECT id FROM planning_applications WHERE council_id=$1 AND reference=$2",
            council_id, app["reference"]
        )

        if existing:
            # Update status if changed
            await db.execute("""
                UPDATE planning_applications
                SET status=$3, decision_date=$4, updated_at=NOW()
                WHERE council_id=$1 AND reference=$2
                  AND (status != $3 OR decision_date IS DISTINCT FROM $4)
            """, council_id, app["reference"],
                app.get("status", "pending"),
                app.get("decision_date"))
        else:
            await db.execute("""
                INSERT INTO planning_applications (
                    council_id, reference, address, postcode, lat, lng,
                    description, application_type, status,
                    submitted_date, decision_date, decision,
                    applicant_name, agent_name, council_url, source
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (council_id, reference) DO NOTHING
            """,
                council_id,
                app["reference"],
                app.get("address"),
                app.get("postcode"),
                lat,
                lng,
                app.get("description"),
                app.get("application_type"),
                app.get("status", "pending"),
                app.get("submitted_date"),
                app.get("decision_date"),
                app.get("decision"),
                app.get("applicant_name"),
                app.get("agent_name"),
                app.get("council_url"),
                app.get("source", "scraper"),
            )
            new_count += 1

    return len(apps), new_count


async def run_council_scraper(db, council: dict, days_back: int = 7):
    """Run the appropriate scraper for a single council."""
    council_id = council["id"]
    name = council["name"]
    system = council["system"]
    portal_url = council["portal_url"] or ""

    # Log start
    log_id = await db.fetchval("""
        INSERT INTO scrape_log (council_id, source, status)
        VALUES ($1, $2, 'running')
        RETURNING id
    """, council_id, f"{system}_scraper")

    try:
        # Scrape
        if system == "idox" and portal_url:
            apps = await scrape_idox_weekly(name, portal_url, days_back)
        elif system == "northgate" and portal_url:
            apps = await scrape_northgate_weekly(name, portal_url, days_back)
        else:
            # No scraper for this system — mark as manual_link
            await db.execute("""
                UPDATE councils SET coverage_source='manual_link', updated_at=NOW()
                WHERE id=$1
            """, council_id)
            await db.execute("""
                UPDATE scrape_log SET status='skipped', finished_at=NOW()
                WHERE id=$1
            """, log_id)
            return

        # Geocode postcodes
        postcodes = [a.get("postcode") for a in apps if a.get("postcode")]
        coords = await bulk_geocode(postcodes) if postcodes else {}

        # Upsert to DB
        found, new = await upsert_applications(db, council_id, apps, coords)

        # Update council
        source = f"{system}_scraper"
        await db.execute("""
            UPDATE councils
            SET coverage_source=$2, last_scraped_at=NOW(), updated_at=NOW()
            WHERE id=$1
        """, council_id, source)

        # Update log
        await db.execute("""
            UPDATE scrape_log
            SET status='success', finished_at=NOW(),
                applications_found=$2, applications_new=$3
            WHERE id=$1
        """, log_id, found, new)

        print(f"  [{name}] ✓ found={found} new={new}")

    except Exception as e:
        print(f"  [{name}] ✗ Error: {e}")
        await db.execute("""
            UPDATE scrape_log
            SET status='failed', finished_at=NOW(), error_message=$2
            WHERE id=$1
        """, log_id, str(e)[:500])


async def run_gov_api_poller(db, days_back: int = 2):
    """Poll the government planning data API."""
    print("\n[Gov API] Polling...")
    log_id = await db.fetchval("""
        INSERT INTO scrape_log (source, status)
        VALUES ('gov_api', 'running')
        RETURNING id
    """)

    try:
        apps = await poll_gov_api(days_back)
        total_new = 0

        for app in apps:
            council_name = app.get("council_name", "")

            # Try to match council by name
            council_id = await db.fetchval(
                "SELECT id FROM councils WHERE name ILIKE $1",
                f"%{council_name}%"
            ) if council_name else None

            if not council_id:
                continue

            # Geocode if needed
            lat = app.get("lat")
            lng = app.get("lng")
            if not lat and app.get("postcode"):
                coords = await bulk_geocode([app["postcode"]])
                coord = coords.get(app["postcode"].replace(" ", "").upper())
                if coord:
                    lat, lng = coord

            result = await db.execute("""
                INSERT INTO planning_applications (
                    council_id, reference, address, postcode, lat, lng,
                    description, application_type, status,
                    submitted_date, decision_date, council_url, source
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT (council_id, reference) DO NOTHING
            """,
                council_id,
                app["reference"],
                app.get("address"),
                app.get("postcode"),
                lat, lng,
                app.get("description"),
                app.get("application_type"),
                app.get("status", "pending"),
                app.get("submitted_date"),
                app.get("decision_date"),
                app.get("council_url"),
                "gov_api",
            )
            if result != "INSERT 0 0":
                total_new += 1

        await db.execute("""
            UPDATE scrape_log
            SET status='success', finished_at=NOW(),
                applications_found=$2, applications_new=$3
            WHERE id=$1
        """, log_id, len(apps), total_new)

        print(f"  [Gov API] ✓ found={len(apps)} new={total_new}")

    except Exception as e:
        print(f"  [Gov API] ✗ Error: {e}")
        await db.execute("""
            UPDATE scrape_log
            SET status='failed', finished_at=NOW(), error_message=$2
            WHERE id=$1
        """, log_id, str(e)[:500])


async def main():
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=3,
        statement_cache_size=0,
        ssl="require"
    )

    start = datetime.utcnow()
    print(f"[{start.isoformat()}] PlanPing scraper starting...")

    async with pool.acquire() as db:
        # 1. Poll government API (fast, few councils but free)
        await run_gov_api_poller(db, days_back=2)

        # 2. Scrape Idox councils
        idox_councils = await db.fetch("""
            SELECT id, name, system, portal_url
            FROM councils
            WHERE system = 'idox'
              AND active = TRUE
              AND portal_url IS NOT NULL
            ORDER BY name
        """)

        print(f"\n[Idox] Scraping {len(idox_councils)} councils...")
        for council in idox_councils:
            await run_council_scraper(db, dict(council), days_back=7)
            await asyncio.sleep(2)  # be polite between councils

        # 3. Scrape Northgate councils
        northgate_councils = await db.fetch("""
            SELECT id, name, system, portal_url
            FROM councils
            WHERE system = 'northgate'
              AND active = TRUE
              AND portal_url IS NOT NULL
            ORDER BY name
        """)

        print(f"\n[Northgate] Scraping {len(northgate_councils)} councils...")
        for council in northgate_councils:
            await run_council_scraper(db, dict(council), days_back=7)
            await asyncio.sleep(2)

        # 4. Mark 'other' councils as manual_link
        await db.execute("""
            UPDATE councils
            SET coverage_source = 'manual_link'
            WHERE system = 'other'
              AND coverage_source = 'pending'
        """)

    elapsed = (datetime.utcnow() - start).seconds
    print(f"\nDone in {elapsed}s.")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())


