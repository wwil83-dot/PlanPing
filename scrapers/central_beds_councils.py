"""
PlanFind — Central Bedfordshire Council (AcolNet) config (2026-08-30).

Real, confirmed evidence: oneoff_batch_recon.py (round 1, landing
page) + oneoff_round3_recon.py (round 3, real advanced search form) +
oneoff_round4_recon.py (round 4, real date-range search submission,
402 real results returned for a 01/07/2026-30/08/2026 window).

REAL, CONFIRMED (not guessed):
  - Real system: AcolNet (Acolaid). Landing page has 4 real search
    modes (quick reference, address, weekly list, more search
    options) — the weekly list requires picking 1 of 87 individual
    parishes AND one specific week, impractical as a primary route.
    "More search options" (reached via a stable, non-session-bound
    RIPNAME URL — see BASE_URL below) is the real district-wide
    advanced search used here instead.
  - Real date fields: regdate1 / regdate2 ("Registration Date
    From/To"), format DD/MM/YYYY, no parish restriction required.
  - Real, CONFIRMED the search FORM's own action URL carries a
    session-bound RIPSESSION token — but since Playwright drives the
    live page/DOM, fill+click handles that automatically, no manual
    token parsing needed.
  - Real results page structure: one <table class="results-table"> per
    application (NOT one row per application in a shared table) — each
    a set of <tr><th>label</th><td>value</td></tr> pairs. Real fields
    confirmed present: Application Number (inside an <a>, text suffix
    " (click for more details)"), Registration Date, Parish Name,
    Location, Statutory Class, Proposal, Case Officer, Decision,
    Obligation Status, Appeal Received Date.
  - Real, CONFIRMED STABLE detail link, NOT session-bound:
    acolnetcgi.gov?ACTION=UNWRAP&RIPNAME=Root.PgeResultDetail&TheSystemkey=NNNNNN
    — genuinely reusable later, unlike the pagination links. A real,
    working pending-recheck mechanism is possible here.
  - Real pagination ("Next"/"Last" text links) IS session-bound
    (RIPSESSION token baked into the href) — must be clicked through
    live via Playwright, cannot be constructed from a formula. Real
    confirmed page size: 10 results per page ("1 to 10 of 402
    Results").
  - HONEST LIMITATION: every real application seen during recon was
    undecided ("This case has not yet been decided" in the Decision
    field). What a DECIDED application's Decision field text actually
    looks like was never observed — _normalise_central_beds_status()
    below is a best-effort substring normaliser, same honest-gap
    pattern as ni_scraper.py's status-vocabulary limitation.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    # TODO: run INSERT_SQL below in the Supabase SQL editor, then
    # `SELECT id FROM councils WHERE name = 'Central Bedfordshire Council';`
    # and replace this None with the real returned id before running
    # central_beds_scraper.py.
    "Central Bedfordshire Council": 528,
}

LANDING_URL = "https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/AcolNetCGI.gov"
BASE_URL = (
    "https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/"
    "acolnetcgi.gov?ACTION=UNWRAP&RIPNAME=Root.pgesearch"
)
DETAIL_BASE = "https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/acolnetcgi.gov"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Central Bedfordshire Council','central-bedfordshire-council','centralbeds_acolnet','england','https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/AcolNetCGI.gov','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'centralbeds_acolnet',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
