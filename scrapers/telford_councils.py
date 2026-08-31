"""
PlanFind — Telford and Wrekin Council config (2026-08-31).

Real, confirmed evidence: backlog_batch_recon2.py captured the real
search form. HONEST LIMITATION: the search was never actually
SUBMITTED during recon — real results-page structure is UNCONFIRMED.
Built defensively (generic result-row detection + heavy diagnostic
logging), same pattern as charnwood_scraper.py before its first live
run.

REAL, CONFIRMED (not guessed):
  - Real page: secure.telford.gov.uk/planningsearch/ — this is DIFFERENT
    from the URL an earlier web-search pass found
    (secure.telford.gov.uk/planning/home.aspx) and different again from
    the council's own public-facing link
    (secure.telford.gov.uk/planning/home.aspx per their own website) —
    user's manually-verified URL used here, per this project's now-
    established preference for manual recon over web search.
  - Real fields: standard ASP.NET WebForms — ctl00_ContentPlaceHolder1_
    txtPlanningKeywords, DCdatefrom, DCdateto, txtDCAgent,
    txtDCApplicant, btnSearchPlanningDetails.

UNCONFIRMED:
  - Real results-page column structure — never actually submitted
    during recon.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Telford and Wrekin Council": 533,
}

SEARCH_URL = "https://secure.telford.gov.uk/planningsearch/"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Telford and Wrekin Council','telford-and-wrekin-council','telford_bespoke','england','https://secure.telford.gov.uk/planningsearch/','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'telford_bespoke',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
