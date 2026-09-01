"""
PlanFind — Ribble Valley Borough Council config (2026-09-01).

Real, confirmed evidence: ribblevalley_recon.py — a genuine end-to-end
pass (search form -> real results -> real detail page), all in one
recon run, with everything working exactly as guessed on the first
try. New intel from the user's own browsing corrected the original
manual recon's "PDF weekly lists only" finding — a real HTML advanced
search exists.

REAL, CONFIRMED (not guessed):
  - Genuinely the simplest tier in the whole project alongside Ipswich,
    West Dunbartonshire, and NI — EVERY step is a plain GET request
    with a query string. No session, no CSRF, no cookie required
    anywhere. NO PLAYWRIGHT NEEDED.
  - Real search fields: location, applicant, developmentDescription
    (free text); decisionType + decisionDate=year (a decision-type-in-
    a-year search, not used here); fromDay/fromMonth/fromYear/toDay/
    toMonth/toYear (the real Decision Date Between range — note this
    filters by DECISION date, not received/submitted date; there is no
    separate received-date-range field on this form).
  - Real results URL: /planningApplication/search/results with all the
    above as query params plus advancedSearch=Search.
  - Real pagination: plain GET offset param `lowerLimit` (0, 10, 20,
    ... — 10 results per page), fully constructible.
  - Real results table: one <tr> per application, cell 1 = reference
    (linked to the real detail page via an opaque internal numeric ID,
    e.g. /planningApplication/38684 — NOT derivable from the reference
    itself, must be taken from each row's real href), cell 2 =
    applicant name (bold) + address on the next line.
  - Real detail page: proposal text in a <p class="first"> right after
    the <h1>; then a clean, uniform <table class="planningTable"> of
    label/value rows: Development address, Applicant, Agent, Officer
    (name/tel/email), Key dates (Received/Registered/Committee, each
    DD/MM/YYYY), Planning Status (e.g. "Decided - Final Decision"),
    Decision (real specific outcome text, e.g. "APPROVED WITH
    CONDITIONS", plus a Date line) — genuinely richer real decision
    detail than most platforms in this project, which usually only
    expose a binary decided/not-decided signal.
  - IMPORTANT: the only date-range search available is DECISION date,
    not received/submitted date. A pending application's submission
    date isn't independently filterable via the search form itself —
    only its Received date, visible once on the detail page's Key
    dates, is available; DAYS_BACK here filters by decision date, so
    recently-SUBMITTED-but-not-yet-decided applications from the
    window won't be found by this scraper without a different search
    strategy. HONEST LIMITATION: undecided applications are likely
    under-covered by this approach — worth flagging for a future
    improvement (e.g. also searching with decisionType blank across a
    wider historical range, or finding a received-date search mode not
    yet discovered).
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Ribble Valley Borough Council": 537,
}

BASE_URL = "https://webportal.ribblevalley.gov.uk"
RESULTS_URL = f"{BASE_URL}/planningApplication/search/results"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Ribble Valley Borough Council','ribble-valley-borough-council','ribblevalley_bespoke','england','https://webportal.ribblevalley.gov.uk/planningApplication/search/advanced','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'ribblevalley_bespoke',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
