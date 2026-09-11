"""
PlanFind — councils confirmed running the same underlying platform as
Fylde Council (2026-09-11).

Real, confirmed evidence via fylde_cluster_recon.py: both councils
below share Fylde's exact search-form field names (DateReceivedFrom/
DateReceivedTo, SearchPlanning, AdvancedSearch, __RequestVerification
Token) AND, after ticking the SearchPlanning checkbox before
submission, produce a real results page with the exact same structure
as Fylde's own (table class="tblResults", pagination link with
aria-label="Next Page."). This is a genuine platform match, not a
visual resemblance — see fylde_cluster_recon.py's module docstring for
the full round-by-round evidence trail.

HONEST LIMITATION: the real result-ROW structure (the exact href
pattern distinguishing Planning applications from other categories,
matching Fylde's own /Planning/Display/ vs /BuildingControl/Display/
split) was NOT directly confirmed — the recon only checked page-level
fingerprints (table class, pagination), not individual row markup.
planning_register_scraper.py's parser is built defensively for this
reason — see that file's own module docstring.

Fill in COUNCIL_DB_IDS with the real Supabase-assigned ids after
running INSERT_SQL below and looking them up, same pattern as every
other new platform in this project.
"""

# REAL, MANUALLY-CONFIRMED base URLs — see fylde_cluster_recon.py's
# CANDIDATES list, kept exactly as originally provided by the user's
# own browsing (not reconstructed from a web search).
PLANNING_REGISTER_COUNCILS = [
    # (council_name, base_url, needs_disclaimer)
    ("Worcester City Council", "https://plan.worcester.gov.uk", False),
    ("Vale of Glamorgan Council", "https://vogonline.planning-register.co.uk", True),
]

# Fill these in with the real Supabase-assigned council ids after
# running the INSERT_SQL below.
COUNCIL_DB_IDS = {
    "Worcester City Council": 554,
    "Vale of Glamorgan Council": 555,
}

INSERT_SQL = """
INSERT INTO councils (name, coverage_source, system)
VALUES
    ('Worcester City Council', 'pending', 'planning_register'),
    ('Vale of Glamorgan Council', 'pending', 'planning_register')
RETURNING id, name;
"""
