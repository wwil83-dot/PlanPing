"""
PlanFind — Fife Council config (2026-09-04).

Real, confirmed evidence: found via a general web search for the same
ArcGIS-FeatureServer pattern that led to Edinburgh, then directly
tested. Genuinely one of the best sources in this whole project —
public, queryable, no scraping/WAF resistance at all (once a real but
non-adversarial obstacle was worked around, see below).

REAL, CONFIRMED (not guessed):
  - TWO real, distinct services exist, both with the identical 8-layer
    structure (Pending Consideration/Pending Decision/Appeal/Permitted/
    Refused/Other/Returned-Invalid-or-Withdrawn/All Apps):
    Planning_Pro and Planning_Applications_LinkGISLIVE. Using
    LinkGISLIVE as the primary source — its own real DATE_UPLOADED
    field showed a timestamp of "yesterday" relative to testing,
    directly confirming it's genuinely synced overnight as its name/
    description promises ("Planning Applications from UNIform...
    updates scheduled overnight via FME"). Planning_Pro appears to be
    an older/parallel view of the same underlying data — not used here
    to avoid redundant double-scraping of what's likely identical
    records.
  - HONEST, REAL OBSTACLE (not adversarial): the service's SSL
    certificate has genuinely EXPIRED — a real accidental
    misconfiguration on Fife's own infrastructure, confirmed via a
    direct SSL error (certificate has expired), not any kind of
    deliberate block. Any legitimate client would hit the same error.
    Requires disabling certificate verification to connect at all —
    same legitimate category of fix already used for West
    Dunbartonshire's old/broken certificate earlier this project.
  - Real, rich fields: REFVAL (reference), ADDRESS (lines separated by
    \\r, same convention already handled for Amber Valley/Edinburgh),
    PROPOSAL, DATE_RECEIVED, DATE_APPLICATION_VALID, APPLICATION_TYPE,
    STATUS (real descriptive text, e.g. "Application Permitted - no
    conditions", "Pending Consideration"), DECISION_ISSUED_DATE,
    DECISION (same real text as STATUS when decided), GIS_STATUS (a
    cleaner categorical field: "Permitted", "Pending Consideration",
    etc.), FURTHER_INFO_PUBLIC_URL (a real, stable link to Fife's own
    public Idox-style detail page at planning.fife.gov.uk).
  - Real date fields are ArcGIS-standard Unix epoch MILLISECONDS
    (integers), not strings — needs division by 1000 before standard
    datetime parsing.
  - Real, best layer to query is index 7 ("All Apps") — returns every
    status in one query rather than needing 7 separate calls.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Fife Council": 333,
}

BASE_URL = "https://arcgis-live-as.fife.gov.uk/server/rest/services/Planning_Applications_LinkGISLIVE/MapServer"
ALL_APPS_LAYER = 7

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Fife Council','fife-council','fife_arcgis','scotland','http://planning.fife.gov.uk/online/','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'fife_arcgis',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
