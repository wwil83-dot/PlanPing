"""
PlanFind — Bridgend County Borough Council (2026-09-11).

Real, confirmed evidence trail — see fylde_cluster_recon.py,
bridgend_results_diagnostic.py, and bridgend_resultsperpage_diagnostic.py:

  - Same search-form platform as Fylde/Worcester/Vale of Glamorgan
    (DateReceivedFrom/DateReceivedTo, AdvancedSearch hidden field,
    __RequestVerificationToken) — but SearchPlanning here is
    hidden-only, no visible checkbox, so no tick is needed before
    submitting (confirmed: "No SearchPlanning checkbox found" during
    recon, and submission still succeeded).
  - Genuinely DIFFERENT results-page platform: table class="table"
    (not Fylde's tblResults), real reference format "P/YY/NNN/TYPE"
    (e.g. P/26/459/FUL), Location/Proposal combined in one cell
    (address then description, separated by blank lines).
  - Confirmed working resultsPerPage=50 shortcut — an in-place update,
    no navigation needed, row count 10 -> 50 confirmed directly.
  - Confirmed real pagination URL pattern:
    /Search/ResultsPage/{page}/{page_size}?module=PLA

Fill in COUNCIL_DB_IDS after running INSERT_SQL below and looking up
the real id, same pattern as every other new platform this project.
"""

BASE_URL = "https://planning.bridgend.gov.uk"
SEARCH_URL = f"{BASE_URL}/Search/Planning/Advanced"

COUNCIL_NAME = "Bridgend County Borough Council"

COUNCIL_DB_IDS = {
    COUNCIL_NAME: 556,
}

INSERT_SQL = """
INSERT INTO councils (name, slug, coverage_source, system)
VALUES
    ('Bridgend County Borough Council', 'bridgend-county-borough-council', 'pending', 'bridgend')
RETURNING id, name;
"""
