"""
PlanFind — Kirklees Council config (2026-08-31).

Real, confirmed evidence: backlog_batch_recon2.py captured the real
Advanced search form. HONEST LIMITATION: the search was never actually
SUBMITTED during recon — real results-page structure is UNCONFIRMED.
Built defensively (generic result-row detection + heavy diagnostic
logging), same pattern as charnwood_scraper.py before its first live
run.

REAL, CONFIRMED (not guessed):
  - Real page: default.aspx?advanced_search=true, standard ASP.NET
    WebForms (postback pattern, __VIEWSTATE handled automatically by
    a real browser — Playwright needed).
  - Real fields: txtAreaOrPostcode, txtApplicantName,
    txtProposalDetails, txtDateFrom, txtDateTo, chkThisWeek (checkbox),
    btnAdvSearch (advanced submit) vs btnBasicSearch (basic submit) —
    two separate submit buttons, must click btnAdvSearch specifically.

UNCONFIRMED:
  - Real results-page column structure — never actually submitted
    during recon.

NOTE: idox_councils.py's original master seed list already contains a
row for "Kirklees Council" under system='idox'
(kirklees.gov.uk/online-applications) — that URL was apparently never
confirmed working in production. The INSERT_SQL below uses
ON CONFLICT (name) DO UPDATE, so running it will correct that existing
row's system to the real confirmed bespoke platform rather than create
a duplicate.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Kirklees Council": None,
}

SEARCH_URL = (
    "https://www.kirklees.gov.uk/beta/planning-applications/"
    "search-for-planning-applications/default.aspx?advanced_search=true"
)

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Kirklees Council','kirklees-council-bespoke','kirklees_bespoke','england','https://www.kirklees.gov.uk/beta/planning-applications/search-for-planning-applications/default.aspx','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'kirklees_bespoke',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
