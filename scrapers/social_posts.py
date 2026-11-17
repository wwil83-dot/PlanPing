"""
social_posts.py - builds a weekly social post pack (Facebook, LinkedIn, X/Bluesky) from REAL
PlanFind data. Posts are DRAFTS for you to review and paste/schedule; nothing is posted automatically.

Env: DATABASE_URL, ANTHROPIC_API_KEY, optional AI_MODEL (default claude-sonnet-5-5), OUT_FILE (default social_pack.md)
"""
import asyncio, os, json, datetime
import asyncpg
from ai_client import Client, model_name

MODEL = model_name("claude-sonnet-5-5")
OUT = os.environ.get("OUT_FILE", "social_pack.md")

SYSTEM = """You write social media posts for PlanFind (planfind.co.uk), a free UK planning application search site.
You are given REAL data. Use only the numbers and names supplied; never invent or round up figures.
Tone: friendly, plain English, useful, not hype. No emojis spam (max 2 per post). No politics, no opinions on whether
developments are good or bad. Never mention applicants or private individuals; addresses are fine only at street/area level.
Every post ends with the relevant link on planfind.co.uk if one is supplied."""

BRIEF = """Using the data below, write a weekly pack:

1. FACEBOOK (local-group friendly, 60-110 words): the most interesting local-interest angle (big or unusual proposals). Invite people to check what is planned near them.
2. LINKEDIN (90-140 words): angle for builders, architects, developers (volume of work types, busiest councils). One short insight, one call to action.
3. X/BLUESKY: three separate posts, each under 270 characters including the link.
4. IMAGE IDEAS: two simple graphic ideas (e.g. a league table card) with the exact text to put on each.

Label each section clearly. DATA:
"""


async def gather(conn):
    d = {}
    d["new_apps_7d"] = await conn.fetchval(
        "SELECT COUNT(*) FROM planning_applications WHERE submitted_date >= current_date - 7")
    d["top_work_types_7d"] = [dict(r) for r in await conn.fetch("""
        SELECT work_type, COUNT(*) n FROM planning_applications
        WHERE submitted_date >= current_date - 7 AND work_type IS NOT NULL AND work_type <> 'unknown'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 6""")]
    d["busiest_councils_7d"] = [dict(r) for r in await conn.fetch("""
        SELECT c.name, c.slug, COUNT(*) n FROM planning_applications a JOIN councils c ON c.id=a.council_id
        WHERE a.submitted_date >= current_date - 7 GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5""")]
    d["notable_new_7d"] = [dict(r) for r in await conn.fetch("""
        SELECT c.name council, a.work_type, left(a.description, 200) description, a.address, a.id
        FROM planning_applications a JOIN councils c ON c.id=a.council_id
        WHERE a.submitted_date >= current_date - 7
          AND a.work_type IN ('large_site','farm_diversification','commercial_conversion','new_dwelling')
          AND a.description IS NOT NULL
        ORDER BY (a.work_type='large_site') DESC, length(a.description) DESC LIMIT 8""")]
    for r in d["notable_new_7d"]:
        r["link"] = f"https://planfind.co.uk/application/{r.pop('id')}"
    d["links"] = {"home": "https://planfind.co.uk", "trends": "https://planfind.co.uk/trends"}
    return d


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    data = await gather(conn)
    await conn.close()
    client = Client()
    text = await client.complete(MODEL, SYSTEM, BRIEF + json.dumps(data, default=str, indent=1), max_tokens=1800)
    stamp = datetime.date.today().isoformat()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"# PlanFind social pack {stamp}\n\n*Drafts - check facts and links before posting.*\n\n{text}\n\n---\nData used:\n```json\n{json.dumps(data, default=str, indent=1)}\n```\n")
    print(text)


asyncio.run(main())
