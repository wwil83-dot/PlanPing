"""
PlanFind — South Derbyshire District Council config (2026-08-31).

Real, confirmed evidence: backlog_batch_recon2.py found this is a
Laravel Livewire + Salesforce-backed platform, with the ENTIRE
32,240-application dataset embedded as structured JSON directly in the
initial page load (no separate API call needed to get the data — it's
already server-rendered into the page). backlog_batch_recon4.py
CONFIRMED real UI interaction genuinely filters it: selecting a real
dateType (1 = Validation Date) fires a real Livewire wire:model.live
reactive update that ENABLES the otherwise disabled="" afterDate/
beforeDate <input type="date"> fields; filling those then filters the
dataset for real — confirmed total dropped from 32,240 (unfiltered)
to 83 (a real ~1-month window).

REAL, CONFIRMED (not guessed):
  - Livewire component real public properties (from the initial
    wire:snapshot): dateType (0=none/3 real options: 1=Validation
    Date, 2=Decision Date, 3=Withdrawn Date), afterDate, beforeDate
    (both YYYY-MM-DD, both genuinely disabled="" until dateType is
    set), reference, proposal, location, status, ward, parish,
    perPage, sortBy.
  - Real per-application JSON fields (embedded directly, Salesforce-
    backed): Name (reference, e.g. "DMPN/2026/0888"), Site_Address__c,
    Status__c, Type__c, Short_Proposal__c, Validated_Date__c,
    Current_Decision_Date__c, Decision_Notice_Sent_Date__c,
    Consultation_Deadline__c, sddc_officer__c (case officer),
    Easting__c/Northing__c, url_reference, and a real, permanent,
    STABLE per-application URL:
    https://southderbyshirepr.force.com/s/planning-application/<salesforce_id>/<url_reference>
  - IMPORTANT: dates use British National Grid Easting/Northing, NOT
    lat/lng directly — real lat/lng conversion needed, or geocode from
    address/postcode like every other platform (simpler, matches
    project convention).
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "South Derbyshire District Council": 535,
}

BASE_URL = "https://planning.southderbyshire.gov.uk/"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('South Derbyshire District Council','south-derbyshire-district-council','south_derbyshire_livewire','england','https://planning.southderbyshire.gov.uk/','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'south_derbyshire_livewire',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
