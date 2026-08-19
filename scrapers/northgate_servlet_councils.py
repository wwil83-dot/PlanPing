"""
PlanFind — Northgate 'ApplicationSearchServlet' family council config
(2026-08-19).

3 of the 4 councils recon'd under Priority 2 — Hartlepool, High Peak,
Staffordshire Moorlands. South Tyneside is deliberately NOT here — real
recon confirmed it's a genuinely different technology (ASP.NET
WebForms, not this simple servlet form) with a dynamically-generated,
one-time results URL, needing its own separate scraper architecture
(see northgate_south_tyneside_scraper.py).

REAL, CONFIRMED evidence backing every field below (not guessed) — full
recon trail in northgate_servlet_family_recon.py and this project's own
session history:
  - All 3 share the identical real URL:
    {base_url}/portal/servlets/ApplicationSearchServlet
  - Real form field names differ slightly: Hartlepool has NO separate
    ReceivedDate pair (only ValidDate/DecisionDate/AppealLodgedDate/
    AppealDecisionDate) — High Peak and Staffordshire Moorlands both
    have ReceivedDate as a genuinely separate field from ValidDate.
    "date_field" below records which single pair each council actually
    uses (ReceivedDate preferred where it exists — matches "when was
    this application actually submitted", the real thing this project
    cares about — ValidDate as the closest real equivalent where it
    doesn't).
  - REAL, CONFIRMED via direct testing: filling MULTIPLE date-range
    pairs simultaneously (e.g. both ValidDate AND DecisionDate AND
    AppealLodgedDate AND AppealDecisionDate at once) produces a
    genuine, honest "did not return any results" response from the
    site itself — not a bug, just an unrealistic AND-combination
    almost no real application would ever satisfy. Only ONE pair must
    ever be filled per search.
  - Real date fields are `readonly="true"` — classic date-picker-
    triggered inputs. Confirmed working workaround: set `.value`
    directly via JS and dispatch a real `change` + `blur` event,
    rather than Playwright's fill() (which correctly refuses non-
    editable fields).
  - Real results table structure differs by council:
      Hartlepool: 3 columns — Reference number | Site location |
        Proposed development. NO decision/status info in the list
        itself at all.
      High Peak / Staffordshire Moorlands: 7 columns — Application
        number | Received date | Valid date | Site location | Proposal
        | Decision | Decision date. Real decision values seen directly:
        "Awaiting Validation", "Application Invalid" — genuine
        Northgate-specific status vocabulary, mapped via the shared
        _normalise_status() convention used across this whole project.
  - Every application reference is a real, stable link containing a
    PKID query parameter (e.g. ?PKID=271998) — confirmed reusable for
    direct detail-page recheck later (same purpose as this project's
    other pending_recheck mechanisms), NOT for the search itself (the
    search results page always lives at the same base
    ApplicationSearchServlet URL, no dynamic path — genuinely different
    from South Tyneside's one-time results URL).
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Hartlepool Borough Council":                None,
    "High Peak Borough Council":                 None,
    "Staffordshire Moorlands District Council":  None,
}

# (council_name, base_url — host only, no path, date_field prefix)
NORTHGATE_SERVLET_COUNCILS = [
    ("Hartlepool Borough Council",               "https://planning.hartlepool.gov.uk",    "ValidDate"),
    ("High Peak Borough Council",                "http://planning.highpeak.gov.uk",       "ReceivedDate"),
    ("Staffordshire Moorlands District Council", "http://publicaccess.staffsmoorlands.gov.uk", "ReceivedDate"),
]

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Hartlepool Borough Council','hartlepool-borough-council','northgate_servlet','england','https://planning.hartlepool.gov.uk/portal/servlets/ApplicationSearchServlet','pending',true),
  ('High Peak Borough Council','high-peak-borough-council','northgate_servlet','england','http://planning.highpeak.gov.uk/portal/servlets/ApplicationSearchServlet','pending',true),
  ('Staffordshire Moorlands District Council','staffordshire-moorlands-district-council','northgate_servlet','england','http://publicaccess.staffsmoorlands.gov.uk/portal/servlets/ApplicationSearchServlet','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'northgate_servlet',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
