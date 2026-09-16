"""
PlanFind — Vale of White Horse & South Oxfordshire (2026-09-13).

Real, confirmed evidence trail — see fylde_cluster_recon.py and
fylde4_diagnostic.py (9 rounds of real, direct testing):

CONFIRMED DIFFERENT PLATFORM from Fylde/Worcester/Vale of Glamorgan/
Welwyn Hatfield, despite sharing some of the same field names
(DateReceivedFrom/DateReceivedTo exist on both platform families, but
everything else diverges):

  - The DateReceivedFrom field sits inside a native HTML5 <details>
    element, collapsed by default — confirmed via direct parent-chain
    inspection. Must set the <details> element's .open property to
    true before the field becomes visible/fillable.
  - Date fields are native HTML5 <input type="date"> — require ISO
    format (YYYY-MM-DD), same requirement as Welwyn Hatfield.
  - No single "SearchPlanning" boolean checkbox — instead a whole list
    of individual PlanningApplicationTypes checkboxes (Full, Outline,
    TPO, etc.). Confirmed real finding: submitting with NONE of these
    ticked returns a genuine, successful search with ZERO results
    ("Search Results (0) - Online Register") — an empty type filter
    appears to mean "match nothing", not "match everything". All real
    checkboxes are ticked before submitting to avoid this.
  - The real submit control says "Apply" (<input type="submit"
    value="Apply">), not "Search" — a different vendor's wording.
  - Results load via an in-page AJAX update (same URL before/after
    click) — NOT a full page navigation, confirmed via direct testing
    (wrapping the click in expect_navigation() times out).
  - A disclaimer gate exists (confirmed button text "Accept", an exact
    match unlike Vale of Glamorgan's partial "Accept & Continue").

HONEST LIMITATION: the real result-ROW structure was never directly
observed with actual data — every real test run returned "(0)" results
because the PlanningApplicationTypes fix (tick all checkboxes) was
only confirmed as a theory, never actually tested against a real
non-empty result set before this scraper was built. The parser below
is built defensively, with clear diagnostics if the expected structure
isn't found, and the FIRST real production run should be checked
closely for these firing.

Fill in COUNCIL_DB_IDS with the real Supabase-assigned ids after
running INSERT_SQL below and looking them up, same pattern as every
other new platform in this project.
"""

# REAL, MANUALLY-CONFIRMED base URLs — see fylde_cluster_recon.py's
# CANDIDATES list, kept exactly as originally provided by the user's
# own browsing (not reconstructed from a web search).
VOWH_SOXON_COUNCILS = [
    ("Vale of White Horse District Council", "https://valeofwhitehorse.planning-register.co.uk"),
    ("South Oxfordshire District Council", "https://southoxfordshire.planning-register.co.uk"),
]

COUNCIL_DB_IDS = {
    "Vale of White Horse District Council": 558,
    "South Oxfordshire District Council": 559,
}

INSERT_SQL = """
INSERT INTO councils (name, slug, coverage_source, system)
VALUES
    ('Vale of White Horse District Council', 'vale-of-white-horse-district-council', 'pending', 'vowh_soxon'),
    ('South Oxfordshire District Council', 'south-oxfordshire-district-council', 'pending', 'vowh_soxon')
RETURNING id, name;
"""
