"""
PlanFind — Walsall (Swift/APAS) config (2026-08-27).

Real, confirmed evidence: oneoff_batch_recon.py (round 1) + 
oneoff_round2_recon.py (round 2, real search submission).

REAL, CONFIRMED (not guessed):
  - Real system: "Web APAS" (Swift Datapro Software Limited) — a
    genuinely different, real vendor from every other platform in this
    project.
  - Real date fields, no id, only a name attribute:
    REGFROMDATE.MAINBODY.WPACIS.1 / REGTODATE.MAINBODY.WPACIS.1 — real
    plain text inputs, format confirmed DD/MM/YYYY via direct
    successful submission.
  - Real search button: SEARCHBUTTON.MAINBODY.WPACIS.1 (name
    attribute, no id).
  - Real, confirmed results page: genuinely simple — "Your search
    returned N matches", real pagination ("Pages: [1] 2 3 4 5 6"),
    real results table with columns Ref No | Description | Location.
  - Real, confirmed 10 results per page based on total count vs pages
    shown (57 matches, 6 pages).
  - Real detail URL confirmed but appears session-bound (embeds a
    real, specific backURL with encoded parameters) — same honest
    limitation as Barrow: no safe pending-recheck mechanism without
    further investigation into whether a stable, reference-only URL
    variant exists.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Walsall Metropolitan Borough Council": 189,
}

BASE_URL = "https://planning.walsall.gov.uk/swift/apas/run/wphappcriteria.display"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Walsall Metropolitan Borough Council','walsall-metropolitan-borough-council','walsall_apas','england','https://planning.walsall.gov.uk/swift/apas/run/wphappcriteria.display','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'walsall_apas',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
