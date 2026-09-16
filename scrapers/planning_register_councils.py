"""
PlanFind — councils confirmed running the same underlying platform as
Fylde Council (2026-09-13).

Real, confirmed evidence via fylde_cluster_recon.py and
fylde4_diagnostic.py: all three councils below share Fylde's exact
search-form field names (DateReceivedFrom/DateReceivedTo,
SearchPlanning, AdvancedSearch, __RequestVerificationToken) AND, after
ticking the SearchPlanning checkbox before submission, produce a real
results page. This is a genuine platform match, not a visual
resemblance — see fylde_cluster_recon.py's module docstring for the
full round-by-round evidence trail.

Welwyn Hatfield (added 2026-09-13) has two genuine platform quirks not
seen on Worcester/Vale of Glamorgan, both confirmed via
fylde4_diagnostic.py's real, repeated test runs:
  - A cookie consent banner overlay must be dismissed before any other
    interaction, or it physically intercepts clicks on the submit
    button ("<div id='ccc-overlay'>...intercepts pointer events").
  - Its date fields are native HTML5 <input type="date">, requiring
    ISO format (YYYY-MM-DD) rather than the DD/MM/YYYY that Worcester/
    Vale of Glamorgan's plain text inputs accept.
Confirmed working end-to-end 6 times in a row (5+ diagnostic rounds,
each landing on a real https://planning.welhat.gov.uk/Search/Results
page) — see planning_register_scraper.py's PlanningRegisterPortal for
how these are handled.

HONEST LIMITATION: the real result-ROW structure (the exact href
pattern distinguishing Planning applications from other categories,
matching Fylde's own /Planning/Display/ vs /BuildingControl/Display/
split) was NOT directly confirmed for ANY of these three councils —
the recon only checked page-level fingerprints (table class,
pagination), not individual row markup, and Welwyn Hatfield's real
results page was never captured at all beyond confirming the URL it
lands on. planning_register_scraper.py's parser is built defensively
for this reason — see that file's own module docstring. Welwyn
Hatfield's FIRST real production run should be checked closely for the
ROW STRUCTURE DIAGNOSTIC firing, since its results-table structure is
genuinely unconfirmed, unlike Worcester/Vale of Glamorgan which have
already run cleanly in production.

Fill in COUNCIL_DB_IDS with the real Supabase-assigned ids after
running INSERT_SQL below and looking them up, same pattern as every
other new platform in this project.
"""

# REAL, MANUALLY-CONFIRMED base URLs — see fylde_cluster_recon.py's
# CANDIDATES list, kept exactly as originally provided by the user's
# own browsing (not reconstructed from a web search).
PLANNING_REGISTER_COUNCILS = [
    # (council_name, base_url, needs_disclaimer, needs_cookie_dismiss, use_iso_dates, uses_list_results)
    ("Worcester City Council", "https://plan.worcester.gov.uk", False, False, False, False),
    ("Vale of Glamorgan Council", "https://vogonline.planning-register.co.uk", True, False, False, False),
    ("Welwyn Hatfield Borough Council", "https://planning.welhat.gov.uk", False, True, True, True),
]

# Fill these in with the real Supabase-assigned council ids after
# running the INSERT_SQL below. Worcester/Vale of Glamorgan's ids are
# already confirmed and unchanged from their existing production run.
COUNCIL_DB_IDS = {
    "Worcester City Council": 554,
    "Vale of Glamorgan Council": 555,
    "Welwyn Hatfield Borough Council": 292,
}

INSERT_SQL = """
INSERT INTO councils (name, slug, coverage_source, system)
VALUES
    ('Welwyn Hatfield Borough Council', 'welwyn-hatfield-borough-council', 'pending', 'planning_register')
RETURNING id, name;
"""
