"""
PlanFind — Stratford-on-Avon (E-Planning v2.11) config (2026-08-27).

Real, confirmed evidence: oneoff_batch_recon.py, oneoff_round2_recon.py,
stratford_search_flow_check.py, stratford_advanced_search_recon.py.

REAL, CONFIRMED (not guessed):
  - Real system: "E-Planning(v2.11)" — a genuinely different vendor
    from every other platform in this project.
  - Two real, separate search interfaces exist: a "Monthly List"
    (Parish + single-Month dropdown, no true date-range) and a
    genuinely richer "Advanced Search" with real native type="date"
    fields — using Advanced Search, since it supports a real from/to
    range directly rather than needing to iterate individual months.
  - Real, CORRECTED field ids: dateApprecFrom / dateApprecTo (the real
    "Date Application Received" fields) — confirmed genuinely
    different from dateAppValidFrom/dateAppValidTo ("Date Application
    Valid"), which was mistakenly targeted first and produced a real,
    confirmed empty search (network capture showed a bare, parameter-
    less GET request) purely because that field wasn't the one
    actually meant to be searched, not any real framework/automation
    limitation. Confirmed directly working: the person running this
    project filled "Date Application Received" by hand and got 99 real
    results with a clean, standard results page. Real native
    type="date" inputs, confirmed appearing TWICE in the DOM (likely a
    responsive mobile/desktop duplicate) — using .first defensively,
    with Playwright's native .fill() (not raw JS value-setting).
  - Real cookie/consent gate exists on the base MonthlyList URL,
    confirmed a plain "Accept" button dismisses it — untested whether
    Advanced Search (reached via /eplanningv2 -> Search -> Advanced
    Search) also requires this same dismissal; handling defensively.
  - Real results page and pagination structure NOT YET directly
    confirmed — never successfully submitted a real Advanced Search
    with actual date values filled in. Genuinely built and tested as
    part of the same work as the scraper itself, not pre-confirmed via
    a separate recon round the way most other platforms in this
    project were.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Stratford-on-Avon District Council": None,
}

BASE_URL = "https://apps.stratford.gov.uk/eplanningv2"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Stratford-on-Avon District Council','stratford-on-avon-district-council','stratford_eplanning','england','https://apps.stratford.gov.uk/eplanningv2/Home/AdvancedSearch','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'stratford_eplanning',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
