"""
PlanFind — Charnwood (Assure platform) config (2026-08-29).

Real, confirmed evidence backing every design decision here — a
genuinely stubborn platform requiring 8+ real diagnostic rounds across
2 days before all pieces confirmed working. Full evidence trail:
charnwood_assure_recon.py, charnwood_search_recon.py,
charnwood_guid_pagination_test.py.

REAL, CONFIRMED (not guessed):
  - Real vendor: "Assure" — a genuinely new platform never seen
    elsewhere in this project. The original roadmap note ("Assure")
    was correct; an earlier web-search-derived URL correction to a
    Northgate/PlanningExplorerAA path was WRONG (confirmed dead,
    ERR_NAME_NOT_RESOLVED) — always verify a URL directly rather than
    trust a plausible-looking search result.
  - Real, required 5-step interaction flow to reach the Monthly List
    results (no shortcuts found despite trying several):
      1. Check #PlanningApplications radio
      2. Click "Weekly / Monthly list" (reveals 2 further sub-options,
         does NOT navigate anywhere itself)
      3. Click "Monthly list" (reveals the real Monthly List form)
      4. Select a real month from the dropdown (option[0] is a
         placeholder "Select a Month yyyy", NOT a real month —
         real months start at option[1])
      5. Check "Validated this month" — REQUIRED. Real, confirmed
         blocking validation error "Please select a status" occurs
         without it, despite the person running this project's own
         successful manual test leaving it unchecked — a genuine,
         unexplained discrepancy between manual and automated
         interaction on this specific platform.
  - Real search button: #ancWeeklyMonthlySearch (a DIFFERENT real
    button from the basic keyword search's #ancBasicSearch, and from
    #aResetSearchTools — 3 real "Search"-adjacent elements exist on
    this page, unscoped text matching genuinely could not
    distinguish them reliably).
  - Real results table: 6 columns — Reference No. | Status |
    Development type | Description | Address | Date Registered.
  - Real "Status" column values are workflow stages, not decisions
    (confirmed real full list from the form's own checkboxes: APPEAL
    DECIDED, APPEAL LODGED, COMPLETE, DEEMED CONSENT, FINAL DECISION,
    REGISTERED, WITHDRAWN) — even "FINAL DECISION" doesn't reveal
    WHICH decision; the real, specific outcome ("Decided: GRANT
    CONDITIONALLY") only appears on the detail page.
  - Real, CONFIRMED STABLE detail URL — genuinely reusable in a
    completely fresh browser session with no shared cookies/state
    (OnlinePlanningOverview?applicationNumber=X&guid=Y). A real,
    working pending-recheck mechanism is possible here, unlike
    several other platforms built this session.
  - Real pagination: PagingClick('N') (0-indexed), confirmed via
    hidden fields PageCount/PageSize/TotalRecords. A genuine, real UI
    click on the page-number link consistently failed to trigger the
    actual AJAX content swap across several attempts (mechanically
    "succeeded" with no error, yet content never changed) — calling
    the real underlying JS function directly via page.evaluate()
    (replicating both statements from the confirmed real onclick
    handler) is the only approach confirmed to work reliably.
  - Real total confirmed via text: "N Results" — appearing after a
    genuine, variable AJAX delay (confirmed anywhere from ~4s to
    never within 15s across different attempts) — a real polling loop
    is necessary, a fixed sleep is not reliable.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Charnwood Borough Council": 526,
}

BASE_URL = "https://planningexplorer.charnwood.gov.uk/Assure/ES/Presentation/Planning/OnLinePlanning/OnlinePlanningSearch"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Charnwood Borough Council','charnwood-borough-council','charnwood_assure','england','https://planningexplorer.charnwood.gov.uk/Assure/ES/Presentation/Planning/OnLinePlanning/OnlinePlanningSearch','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'charnwood_assure',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
