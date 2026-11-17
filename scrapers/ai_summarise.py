"""
ai_summarise.py - writes a short plain-English "what this means for people" note
for planning applications and stores it in planning_applications.ai_summary.

Default scope (cheap, avoids thin pages): PENDING applications submitted in the last
SUMMARY_DAYS days whose work_type is one of the notable tags. Newest first.

Env:
  DATABASE_URL        (required)
  ANTHROPIC_API_KEY or GROQ_API_KEY (with AI_PROVIDER=groq and AI_MODEL=<groq model>)
  SUMMARY_LIMIT       max rows per run                (default 300)
  SUMMARY_DAYS        only apps submitted this recently (default 60)
  SUMMARY_TAGS        comma list of work_type keys     (default large_site,farm_diversification,commercial_conversion,new_dwelling)
  SUMMARY_ALL_TYPES   "1" = ignore SUMMARY_TAGS, do any pending app
  AI_MODEL            (default claude-haiku-5-5)
  DRY_RUN             "1" = print summaries, do not save
"""
import asyncio, os, sys, re
import asyncpg
from ai_client import Client, model_name

MODEL = model_name("claude-haiku-5-5")
LIMIT = int(os.environ.get("SUMMARY_LIMIT", "300"))
DAYS = int(os.environ.get("SUMMARY_DAYS", "60"))
TAGS = [t.strip() for t in os.environ.get(
    "SUMMARY_TAGS", "large_site,farm_diversification,commercial_conversion,new_dwelling"
).split(",") if t.strip()]
ALL_TYPES = os.environ.get("SUMMARY_ALL_TYPES") == "1"
DRY = os.environ.get("DRY_RUN") == "1"
CONCURRENCY = 5

SYSTEM = """You write short plain-English notes for a UK planning-applications website.
Explain what a planning application proposes and what it could mean for people who live nearby or work locally.

Rules:
- Use ONLY the facts given. Never invent sizes, numbers, dates, names, objections or outcomes.
- 2 to 3 sentences, maximum 70 words, in everyday language. Explain jargon (e.g. "change of use", "outbuilding", "lawful development certificate").
- Say what is proposed first. Then, only if it follows from the text, what it might mean day to day (e.g. building work, more homes, new business use). Use cautious wording: "could", "may".
- Do not say whether it will or should be approved. Do not give legal advice. Do not mention the council's decision unless it is stated.
- Do not name or guess at the applicant or any person.
- If the description is too vague to explain, reply with exactly: SKIP
- Output the note only: no heading, no quotes, no preamble."""


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip('"').strip()


async def summarise(client, row, sem):
    prompt = (
        f"Council: {row['council']}\nAddress: {row['address']}\n"
        f"Application type: {row['application_type'] or 'unknown'}\n"
        f"Description: {row['description']}"
    )
    async with sem:
        for attempt in range(3):
            try:
                text = await client.complete(MODEL, SYSTEM, prompt, max_tokens=200)
                return row["id"], clean(text)
            except Exception as e:
                if attempt == 2:
                    print(f"  ! id {row['id']}: {e}", flush=True)
                    return row["id"], None
                await asyncio.sleep(2 * (attempt + 1))


async def main():
    client = Client()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    rows = await conn.fetch(
        """
        SELECT a.id, a.address, a.description, a.application_type, c.name AS council
        FROM planning_applications a
        JOIN councils c ON c.id = a.council_id
        WHERE a.ai_summary IS NULL
          AND a.status = 'pending'
          AND a.description IS NOT NULL AND length(a.description) >= 25
          AND a.submitted_date >= current_date - $1::int
          AND ($2::boolean OR a.work_type = ANY($3::text[]))
        ORDER BY a.submitted_date DESC
        LIMIT $4
        """,
        DAYS, ALL_TYPES, TAGS, LIMIT,
    )
    print(f"{len(rows)} applications to summarise (model {MODEL}, dry={DRY})", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(summarise(client, r, sem) for r in rows))
    saved = skipped = failed = 0
    by_id = {r["id"]: r for r in rows}
    for app_id, text in results:
        if text is None:
            failed += 1
            continue
        if text.upper().startswith("SKIP") or len(text) < 30 or len(text) > 600:
            skipped += 1
            if not DRY:  # mark so we don't retry endlessly
                await conn.execute(
                    "UPDATE planning_applications SET ai_summary = '', ai_summary_at = now() WHERE id = $1", app_id)
            continue
        if DRY:
            print(f"\n[{by_id[app_id]['council']}] {by_id[app_id]['description'][:140]}\n  -> {text}", flush=True)
        else:
            await conn.execute(
                "UPDATE planning_applications SET ai_summary = $2, ai_summary_at = now() WHERE id = $1",
                app_id, text)
        saved += 1
    print(f"DONE saved={saved} skipped={skipped} failed={failed}", flush=True)
    await conn.close()


asyncio.run(main())
