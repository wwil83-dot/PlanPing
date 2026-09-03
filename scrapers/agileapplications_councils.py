"""
PlanFind — agileapplications.co.uk council config (2026-08-21).

3 councils on one shared platform: Middlesbrough, Flintshire, Cannock
Chase. Real, confirmed evidence backing every design decision here —
see priority3_recon.py for the full recon trail.

REAL, CONFIRMED (not guessed):
  - Real URL pattern: {base}/{council-slug}/search-applications/results
    ?criteria={JSON}&page=1, where JSON is a plain (not URL-safe-only)
    object like {"status":"registered","registrationDateFrom":"...",
    "registrationDateTo":"..."} — real user-supplied URLs confirmed
    this exact shape works directly, no form interaction needed at all.
  - This is a real AngularJS SPA (confirmed via CSP headers referencing
    azurewebsites.net, and the "ng-table"/"sunagile.com" markup) — a
    plain httpx GET only ever returns the empty app shell (confirmed
    directly: 2,495 chars, identical across all 3 councils, just the
    HTML wrapper). A real browser (Playwright) is required to get
    genuine application data — same reasoning as every SPA-based
    platform elsewhere in this project.
  - Real table structure: an AngularJS "ng-table" component. TWO
    identical duplicate <table> elements exist on the page (a real,
    common responsive-view pattern) — only the first is parsed.
    Real, distinct row classes confirmed directly from captured
    markup: the header row has class "ng-table-sort-header", a filter-
    input row has class "ng-table-filters" (NOT real data — each cell
    is just an empty text-filter input), and REAL DATA rows have class
    "animate-repeat" specifically — this is the reliable, confirmed
    real selector for genuine application rows.
  - Real column sets differ per council, confirmed directly:
      Flintshire (8 cols): Reference, Proposal, Location, Registration
        date, Decision, Decision date, Ward, Grid reference
      Cannock (5 cols): Reference, Proposal, Location, Registration
        date, Decision date (no separate Decision column at all)
      Middlesbrough: similar to Cannock's smaller set, "Planning
        reference" not just "Reference" — confirmed a real, genuine
        label variation, not assumed identical to the other two.
    Matched by real header text at scrape time, not fixed position,
    same discipline as northgate_servlet_scraper.py.
  - Real "determined" (decided) status filter confirmed available too,
    via the same URL shape with "status":"determined",
    "decisionDateFrom"/"decisionDateTo" instead of "registered"/
    registrationDate — not yet tested directly, but the SAME real
    pattern the user's own original research already confirmed working
    for at least the "registered" variant, and Idox/getApplications
    both have prior precedent for the two-status pattern (received vs
    decided) working the same shape.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Middlesbrough Council":              202,
    "Flintshire County Council":          487,
    "Cannock Chase District Council":     488,
    "Rugby Borough Council":              491,
    "Dudley Metropolitan Borough Council": 191,
    "Peterborough City Council":           216,
    # ADDED 2026-09-03 — confirmed via agile_criteria_url_test.py: same
    # domain/company as the 6 above, but their real front-facing UI
    # (radio buttons, date-range picker, a Terms & Conditions gate)
    # looked structurally different from the confirmed criteria-URL +
    # ng-table pattern the other 6 use. Tested directly: the SAME
    # ?criteria={JSON}&page=1 URL shape works identically here too (20
    # real animate-repeat rows returned for both, real references/
    # addresses/proposals/dates confirmed) — the visible form is just
    # an alternate UI on the same underlying API, not a different
    # platform generation. Genuinely new — no existing DB rows found.
    # NOTE on naming: the T&Cs consent banner says "Pembrokeshire Coast
    # National Park" but the page's own footer copyright says
    # "Pembrokeshire County Council" — genuinely two separate planning
    # authorities in Wales (the Park covers only the coastline). Real,
    # confirmed sample data included an application at Narberth, an
    # inland town well outside the Park boundary — direct evidence this
    # portal's real data is the full county's, not just the Park's; the
    # T&Cs banner text is most likely shared/generic boilerplate. Named
    # accordingly below.
    "Pembrokeshire County Council":        545,
    "Slough Borough Council":              546,
}

# (council_name, agileapplications council-slug)
AGILE_COUNCILS = [
    ("Middlesbrough Council",              "middlesbrough"),
    ("Flintshire County Council",          "flintshire"),
    ("Cannock Chase District Council",     "cannock"),
    ("Rugby Borough Council",              "rugby"),
    ("Dudley Metropolitan Borough Council", "dudley"),
    ("Peterborough City Council",           "peterborough"),
    ("Pembrokeshire County Council",        "pembrokeshire"),
    ("Slough Borough Council",              "slough"),
]

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Middlesbrough Council','middlesbrough-council','agileapplications','england','https://planning.agileapplications.co.uk/middlesbrough/search-applications','pending',true),
  ('Flintshire County Council','flintshire-county-council','agileapplications','wales','https://planning.agileapplications.co.uk/flintshire/search-applications','pending',true),
  ('Cannock Chase District Council','cannock-chase-district-council','agileapplications','england','https://planning.agileapplications.co.uk/cannock/search-applications','pending',true),
  ('Rugby Borough Council','rugby-borough-council','agileapplications','england','https://planning.agileapplications.co.uk/rugby/search-applications','pending',true),
  ('Dudley Metropolitan Borough Council','dudley-metropolitan-borough-council','agileapplications','england','https://planning.agileapplications.co.uk/dudley/search-applications','pending',true),
  ('Peterborough City Council','peterborough-city-council','agileapplications','england','https://planning.agileapplications.co.uk/peterborough/search-applications','pending',true),
  ('Pembrokeshire County Council','pembrokeshire-county-council','agileapplications','wales','https://planning.agileapplications.co.uk/pembrokeshire/search-applications','pending',true),
  ('Slough Borough Council','slough-borough-council','agileapplications','england','https://planning.agileapplications.co.uk/slough/search-applications','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'agileapplications',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
