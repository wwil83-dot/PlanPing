"""
PlanFind — OcellaWeb platform config (2026-08-25).

4 councils sharing one real platform, confirmed via ocellaweb_family_recon.py
and ocellaweb_results_recon.py: identical real field names across all 4
(reference, location, applicant, agent, undecided, receivedFrom,
receivedTo, decidedFrom, decidedTo, area), genuinely simple structure —
no disclaimer gate, no JS-click pagination, no card layout, no iframes.

REAL, CONFIRMED (not guessed):
  - Real date format: DD-MM-YY (2-digit year), explicitly stated on
    the page itself — genuinely different from every other platform in
    this project.
  - Real search button: an <input type="submit" value="Search">, not
    a <button> tag — confirmed via a real, direct timeout when the
    unscoped "button:has-text('Search')" selector matched an unrelated
    element elsewhere on the page (same category of bug as Cherwell's
    own search-button fix). Scoped specifically to the form containing
    the date fields.
  - Real results table: plain <table>, real header row using <th>,
    columns Reference | Location | Proposal | Received | Type |
    Status. Only directly confirmed for Great Yarmouth — the other 3
    share identical form fields but haven't been individually search-
    tested; handling any real per-council quirks defensively if they
    surface in production, same approach already proven for the
    "Search/Advanced" family.
  - Real, permanent, reusable detail URL:
    {base}/planningDetails?reference={reference}&from=planningSearch
    — genuinely simple and stable, unlike Barrow's session-bound URLs.
    A real pending-recheck mechanism is possible here.
  - Real "Status" column contains genuine decision outcomes when
    decided (confirmed value: "NO OBJECTION"), not just a workflow
    stage — "Undecided" is the real pending state. Same real "no
    objection = approved-ish" precedent already established for the
    Northgate servlet family's own Runnymede data.
  - Havering and Hillingdon are both on the real, confirmed "missing
    13" London boroughs list from an earlier coverage audit — genuine
    value if this platform works cleanly for all 4.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Great Yarmouth Borough Council":     305,
    "South Holland District Council":     519,
    "London Borough of Havering":         227,
    "London Borough of Hillingdon":       238,
}

# (council_name, base_url)
OCELLAWEB_COUNCILS = [
    ("Great Yarmouth Borough Council",
     "https://planning.great-yarmouth.gov.uk"),
    ("South Holland District Council",
     "https://planning.sholland.gov.uk"),
    ("London Borough of Havering",
     "https://development.havering.gov.uk"),
    ("London Borough of Hillingdon",
     "https://planning.hillingdon.gov.uk"),
]

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Great Yarmouth Borough Council','great-yarmouth-borough-council','ocellaweb','england','https://planning.great-yarmouth.gov.uk/OcellaWeb/planningSearch','pending',true),
  ('South Holland District Council','south-holland-district-council','ocellaweb','england','https://planning.sholland.gov.uk/OcellaWeb/planningSearch','pending',true),
  ('London Borough of Havering','london-borough-of-havering','ocellaweb','england','https://development.havering.gov.uk/OcellaWeb/planningSearch','pending',true),
  ('London Borough of Hillingdon','london-borough-of-hillingdon','ocellaweb','england','https://planning.hillingdon.gov.uk/OcellaWeb/planningSearch','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'ocellaweb',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
