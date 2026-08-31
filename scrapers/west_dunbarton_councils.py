"""
PlanFind — West Dunbartonshire Council config (2026-08-31).

Real, confirmed evidence: backlog_batch_recon2.py — BOTH the weekly-
list picker AND a direct date-range results submission were captured,
plus the real detail-link mechanism was found directly in the raw
HTML (not guessed).

REAL, CONFIRMED (not guessed):
  - Genuinely the simplest platform in the whole project alongside
    Ipswich and NI — every step is a plain GET request, no session/
    CSRF/cookie needed anywhere. NO PLAYWRIGHT NEEDED.
  - Real results URL: dcdisplayinitial.asp, accepts vDateRcvFr /
    vDateRcvTo (format DD/MM/YYYY) as genuine working date-range
    filters — confirmed because the real results returned (5
    applications, references DC26/159 through DC26/165) matched the
    requested 01/08/2026-30/08/2026 window, NOT the unrelated
    WeekEnding param value left in the URL. WeekEnding appears to be
    vestigial/required-but-ignored when vDateRcvFr/To are present —
    kept in the query string as a harmless dummy value.
  - Real, CONFIRMED STABLE detail link, no session needed at all: the
    raw HTML's hidden per-application form reveals `vUPRN` is simply
    the application reference itself (e.g. value="DC26/164/FUL"), and
    `vPassword` is empty. Detail URL is fully constructible:
    dcdisplayfullx.asp?vUPRN=<reference>&vPassword=&View1=View
  - Real results list structure: NOT one shared table — one separate
    <table> per application (2-row: header then data), each containing
    only Address + Application Number. No description, status, or date
    at the list level.
  - HONEST LIMITATION: the actual detail page (dcdisplayfullx.asp) was
    never directly captured during recon — my own attempt to fetch it
    for verification was blocked by robots.txt at the fetch-tool level
    (separate from whatever the production Playwright/httpx scraper
    may or may not encounter). The list-level fields (reference,
    address) are fully confirmed; detail-page fields (proposal, date
    received, status) are BEST-EFFORT parsing based on common patterns
    seen across this project's other bespoke Scottish council portals,
    NOT yet confirmed against real HTML. Watch the first live run
    closely — same "confirmed clean production run before promotion"
    discipline as every other platform in this project.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    # TODO: run INSERT_SQL below in the Supabase SQL editor, then
    # `SELECT id FROM councils WHERE name = 'West Dunbartonshire Council';`
    # and replace this None with the real returned id before running
    # west_dunbarton_scraper.py.
    "West Dunbartonshire Council": None,
}

RESULTS_URL = "https://apps.west-dunbarton.gov.uk/dcdisplayinitial.asp"
DETAIL_URL = "https://apps.west-dunbarton.gov.uk/dcdisplayfullx.asp"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('West Dunbartonshire Council','west-dunbartonshire-council','west_dunbarton_asp','scotland','https://apps.west-dunbarton.gov.uk/dcweekly_listx.asp','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'west_dunbarton_asp',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
