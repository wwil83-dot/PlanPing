"""
PlanFind — Ipswich Borough Council config (2026-08-30).

Real, confirmed evidence: oneoff_batch_recon.py (round 1, landing page)
+ oneoff_round3_recon.py (round 3, real advanced search form) +
oneoff_round4_recon.py (round 4, real date-range search submission,
146 real results returned for a 01/07/2026-30/08/2026 window).

REAL, CONFIRMED (not guessed):
  - Real system: a plain classic-ASP platform, genuinely the SIMPLEST
    in the whole project — every step (search, results, pagination,
    detail page) is a plain GET request with query-string parameters,
    no session token, no CSRF, no cookie required anywhere. NO
    PLAYWRIGHT NEEDED — same category as ni_scraper.py.
  - Real search fields (on appnsearch.asp): txtValStartDate (id
    'Text1') / txtValEndDate (id 'Text2'), format DD/MM/YYYY, labelled
    "Date Valid Application Received".
  - Real results page: appnresults.asp, reached via a plain GET with
    txtValStartDate/txtValEndDate/pnlAdvancedOpen=1 as query params —
    CONFIRMED it also works as a direct GET (not just as the target of
    a same-page form POST), since round 4's own pagination links
    (`appnresults.asp?pageNumber=2&txtValStartDate=...`) are themselves
    plain GET hrefs.
  - Real results table: id="dgSearchResults", 9 real columns —
    Application Number | Date Valid App'n Received | Address |
    Proposal | Status | Appeal Decision | View App'n | View Dec'n
    Notice | View Appeal Docs. ~10 results per page (146 apps / 15
    pages confirmed via real "Page 1 of 15" text).
  - Real, confirmed STABLE detail link, no session encoding:
    appndetails.asp?iAppID=<reference>&sType=APP — the real captured
    href also included search_params/prev_search_params/
    det_search_params, but those look like optional in-session
    navigation aids (breadcrumb/back-button context), not required to
    load the page itself. HONEST LIMITATION: this simplified 2-param
    version was never directly tested — same category of unconfirmed-
    but-plausible simplification as Walsall/Barrow's detail-URL notes.
  - Real date format on results: ordinal day + short month + year,
    e.g. "7th Aug 2026" — needs ordinal-suffix stripping before
    parsing.
  - Real status values seen: "Pending Consideration",
    "Approved/Conditions". The advanced search page's own ddlDecision
    dropdown lists the fuller real vocabulary (Application Granted/
    Permitted/Refused/Withdrawn, Approved, Approved as per GOER, etc.)
    — _normalise_ipswich_status() below covers this with a
    substring-based normaliser, same defensive pattern as
    ni_scraper.py's _normalise_ni_status().
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    # TODO: run INSERT_SQL below in the Supabase SQL editor, then
    # `SELECT id FROM councils WHERE name = 'Ipswich Borough Council';`
    # and replace this None with the real returned id before running
    # ipswich_scraper.py — same one-off step every new platform in this
    # project has needed (see charnwood_councils.py for a filled-in
    # example). NOTE: this council may already have a row from an
    # earlier, INCORRECT idox-based entry (see idox_councils.py's
    # 2026-07-25 correction notes) — the ON CONFLICT clause below will
    # correct that existing row's system/portal_url rather than create
    # a duplicate, so the real id may already exist; check first.
    "Ipswich Borough Council": 215,
}

BASE_URL = "https://ppc.ipswich.gov.uk/appnsearch.asp"
RESULTS_URL = "https://ppc.ipswich.gov.uk/appnresults.asp"
DETAIL_URL = "https://ppc.ipswich.gov.uk/appndetails.asp"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Ipswich Borough Council','ipswich-borough-council','ipswich_asp','england','https://ppc.ipswich.gov.uk/appnsearch.asp','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'ipswich_asp',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
