"""
PlanFind — Open Digital Planning Register additional councils config
(2026-09-01): Barnet and Buckinghamshire.

Real, confirmed evidence: medway_councils.py already documented this
is a shared, multi-council platform (planningregister.org) — Medway
was just one council slug among several. Real, direct confirmation via
web search + fetch that both Barnet (planningregister.org/barnet) and
Buckinghamshire (planningregister.org/buckinghamshire) have live
registers with the identical real structure already proven for
Medway — same "Beta"/pilot framing, same real caveat text ("Not all
planning applications are available on this register"), same
recently-published-applications listing with page-number pagination.

CONTEXT: both were found while investigating alternate routes for
councils blocked on their own Idox instance
(idox_candidate_verify.py's 15 parked failures — a confirmed
deterministic per-target network block, NOT rate-limiting, NOT simple
datacenter-IP blocking, since a genuine residential proxy ALSO failed
identically). Barnet is one of those 15 parked councils — this ODP
register is a genuinely separate, unrelated piece of infrastructure
(different domain entirely) and may not be affected by whatever blocks
publicaccess.barnet.gov.uk. Buckinghamshire's own Idox instance
(publicaccess.buckinghamshire.gov.uk) was ALSO one of the 15 parked
failures — same reasoning applies.

Kept as a SEPARATE script/config from medway_councils.py /
medway_scraper.py deliberately — Medway's job is already live and
scheduled nightly; safer not to touch a working production job while
adding new councils to the same underlying platform. A future cleanup
could merge all three into one proper shared-platform scraper (matching
getapplications_scraper.py's multi-council architecture) without
changing behaviour for any of them.

HONEST LIMITATION (same as Medway): this is a known-incomplete pilot
register — real, official text: "Not all planning applications are
available on this register."
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "London Borough of Barnet": 241,
    "Buckinghamshire Council": 381,
}

# (council_name_as_in_supabase_db, planningregister.org slug)
ODP_COUNCILS = [
    ("London Borough of Barnet", "barnet"),
    ("Buckinghamshire Council", "buckinghamshire"),
]

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('London Borough of Barnet','london-borough-of-barnet','odp_register','england','https://planningregister.org/barnet','pending',true),
  ('Buckinghamshire Council','buckinghamshire-council','odp_register','england','https://planningregister.org/buckinghamshire','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'odp_register',
  active = true,
  portal_url = EXCLUDED.portal_url
RETURNING id, name;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
