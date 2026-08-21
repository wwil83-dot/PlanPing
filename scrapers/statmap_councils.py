"""
PlanFind — statmap.co.uk/horizoNext council config (2026-08-21).

2 councils on one shared platform: West Lindsey, East Staffordshire.
Real, confirmed evidence backing every design decision — see
priority3_recon.py, statmap_weeklylist_recon.py, statmap_results_recon.py
and statmap_weeklydate_recon.py for the full recon trail.

REAL, CONFIRMED (not guessed):
  - This is a real React/MUI DataGrid SPA. The direct, useful path is
    NOT the "Weekly Lists" tab itself (which just lists downloadable
    weekly report ENTRIES, not application data) — it's the real, clean
    URL each weekly-list entry links to:
      {base}/planningapplications/?weeklyListDate=YYYY-MM-DD
    Confirmed directly returning a real, populated MUI DataGrid of
    individual applications for that week — no form interaction
    needed at all once you know this URL shape, similar to
    agileapplications.co.uk's direct-URL simplicity.
  - Real, confirmed via direct testing: filling the Weekly Lists
    search form and submitting shows a genuine list of REPORT entries
    (e.g. "Weekly Planning List - 2026-08-17"), each a real <a> link
    to the above URL pattern — the actual weekly list "dates" that
    exist are whatever real Monday-anchored dates the council has
    published, not guaranteed for every Monday.
  - Real column structure confirmed via data-field attributes (more
    reliable than aria-label text, which varies slightly between
    councils — e.g. "Proposal" vs "Proposal Details"):
      West Lindsey: name, initialAppRef, address, proposal,
        receivedDate, status, decision
      East Staffordshire: name, address, proposal, receivedDate,
        status, decision, decisionDate, applicationTypeId
    Core fields (name, address, proposal, receivedDate, status,
    decision) present on both — matched by data-field, not fixed
    position, same discipline as every other platform here.
  - Real, important distinction confirmed directly: "status" is a
    WORKFLOW STAGE (e.g. "Live"), NOT the real decision outcome —
    the separate "decision" field holds that (real confirmed values:
    "PENDING", "No Objection" — East Staffordshire's data appears to
    include real consultation RESPONSES to other authorities'
    applications, not exclusively East Staffordshire's own directly
    decided applications, worth keeping in mind).
  - Real, permanent detail-page id confirmed: the MUI DataGrid row's
    own `data-id` attribute matches directly to
    {base}/planningapplications/{id} — a real, stable numeric id, NOT
    a session-bound token (genuinely simpler than South Tyneside's
    dynamic XMLLoc situation).
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "West Lindsey District Council":       None,
    "East Staffordshire Borough Council":  None,
}

# (council_name, base_url)
STATMAP_COUNCILS = [
    ("West Lindsey District Council",
     "https://westlindsey-publicportal.statmap.co.uk/horizoNext/publicportal"),
    ("East Staffordshire Borough Council",
     "https://eaststaffs-publicportal.statmap.co.uk/horizoNext/publicportal"),
]

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('West Lindsey District Council','west-lindsey-district-council','statmap_horizonext','england','https://westlindsey-publicportal.statmap.co.uk/horizoNext/publicportal','pending',true),
  ('East Staffordshire Borough Council','east-staffordshire-borough-council','statmap_horizonext','england','https://eaststaffs-publicportal.statmap.co.uk/horizoNext/publicportal','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'statmap_horizonext',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
