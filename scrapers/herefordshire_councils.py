"""
PlanFind — Herefordshire Council config (2026-08-28).

Real, confirmed evidence: oneoff_batch_recon.py, oneoff_round2_recon.py,
herefordshire_real_results_recon.py, herefordshire_weekly_list_recon.py,
herefordshire_wide_range_test.py.

REAL, CONFIRMED (not guessed) — genuinely one of the simplest
platforms in this whole project:
  - The "Search applications" tab's date-range search consistently
    leads to an autocomplete/typeahead dropdown rather than real
    results, regardless of approach tried (JS value-setting, native
    .fill(), scoped Search button clicks) — genuinely a dead end.
  - The person running this project found a completely different,
    much simpler real tab: "Weekly list" — producing a real, clean
    table (Application number | Site address | Description | Type |
    Status | Comments by).
  - Real, confirmed: BOTH the search submission AND pagination can be
    done via pure, direct URL construction — no clicking needed at
    all beyond the initial page load:
      {base}/planning-and-building-control/planning-search
        ?search-service=search&search-source=search&search-item=
        &date-to={YYYY-MM-DD}&search-term=&date-from={YYYY-MM-DD}
        &status=all&weeklyParishSearch=Weekly+parish+search
        &offset={0, 10, 20, ...}
  - Real, confirmed a genuine wide date range (30 days, not just a
    single week) works fine directly in one request.
  - Real, confirmed total-count text: "Showing planning applications 1
    to 10 of 69..." — reliably parseable for pagination control.
  - Real, permanent, reference-based detail URL confirmed directly
    inside each real reference link (?id={reference_number}) — a
    genuine, safe pending-recheck mechanism is possible here, unlike
    several other platforms built this session.
  - Real "Status" column confirmed as a workflow stage ("Valid
    (Undecided)"), not a final decision — same discipline as
    everywhere else in this project.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Herefordshire Council": None,
}

BASE_URL = "https://www.herefordshire.gov.uk"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Herefordshire Council','herefordshire-council','herefordshire_weekly_search','england','https://www.herefordshire.gov.uk/planning-and-building-control/planning-search','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'herefordshire_weekly_search',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
