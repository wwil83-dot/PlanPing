"""
PlanFind — Redcar and Cleveland Borough Council config (2026-08-31).

Real, confirmed evidence: backlog_batch_recon2.py captured the real
Advanced search form. HONEST LIMITATION: the search was never actually
SUBMITTED during recon — real results-page structure is UNCONFIRMED.
Built defensively (generic result-row detection + heavy diagnostic
logging), same "confirmed clean production run before promotion"
pattern as charnwood_scraper.py before its first live run.

REAL, CONFIRMED (not guessed):
  - Real search page: /Search/Planning/Advanced
  - Real fields: ApplicationNumber, Address, Proposal, AgentsName,
    ApplicantsName, DateReceivedFrom/To, DateIssuedFrom/To,
    DateAppealFrom/To — three SEPARATE date-range pairs (received/
    issued/appeal), not just one.
  - Real form POSTs to /Search/Results with a genuine ASP.NET
    anti-forgery token (__RequestVerificationToken) — Playwright
    fill+submit needed (not plain httpx) since the browser handles the
    token automatically.
  - Same URL SHAPE (/Search/Advanced, /Search/Results) as Fylde
    Council — likely the same commercial platform vendor. Worth
    cross-referencing once either council's real results structure is
    confirmed; the other is likely near-identical.

UNCONFIRMED:
  - Real results-page column structure — never actually submitted
    during recon. This scraper's result parsing is generic/defensive
    and WILL need real-world verification on first live run.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Redcar and Cleveland Borough Council": None,
}

SEARCH_URL = "https://planning.redcar-cleveland.gov.uk/Search/Planning/Advanced"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Redcar and Cleveland Borough Council','redcar-and-cleveland-borough-council','redcar_cleveland_bespoke','england','https://planning.redcar-cleveland.gov.uk/Search/Planning/Advanced','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'redcar_cleveland_bespoke',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
