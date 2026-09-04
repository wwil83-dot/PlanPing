"""
PlanFind — Edinburgh (City of Edinburgh Council) config (2026-09-03).

Real, confirmed evidence: found while investigating an alternate route
around Edinburgh's own Idox instance (never directly tested for
blocking, but the user found this route independently while looking
for alternatives). edinburgh_webmap_recon3.py resolved the real
FeatureServer layers behind the council's own "Planning Weekly Lists"
ArcGIS Web AppBuilder map and queried each directly — genuinely one of
the richest sources in this whole project: real decision text
(Granted/Refused/EIA Not Required, not just a binary decided flag),
real proposal text, real coordinates, and a real direct link back to
Edinburgh's own Idox detail page for each application.

REAL, CONFIRMED (not guessed):
  - Web map item (org-hosted, NOT the generic public arcgis.com):
    https://cityofedinburgh.maps.arcgis.com/sharing/rest/content/items/
    af6b177c787b4831b6745ee149cf71fd/data?f=json
  - IMPORTANT ARCHITECTURAL POINT: Edinburgh does NOT keep one
    persistent, growing table. It publishes a NEW, separately-named
    FeatureServer layer every week — real confirmed examples:
    "Applications 24 August 2026", "Decisions 24 August 2026",
    "Applications 31 August 2026", "Decisions 31 August 2026" — and
    only the last 2 weeks' worth exist in the web map at any given
    time (older weeks' layers are removed, not accumulated). A
    scraper CANNOT hit one fixed URL; it must re-fetch the web map's
    real operationalLayers list on every run and process whatever
    "Applications "/"Decisions "-prefixed layers currently exist,
    never a hardcoded date.
  - Real Applications layer fields: FID, Appno (reference), AppType,
    Address (lines separated by \\r, same convention as several other
    Idox-derived platforms in this project), Applicant, Registered
    (date, format "DD-Mon-YY"), Proposal, Details (a real, direct link
    to the underlying Idox detail page — citydev-portal.edinburgh.gov.uk
    /idoxpa-web/applicationDetails.do?...), scale, XCOORD/YCOORD
    (British National Grid, EPSG:27700 — NOT lat/lng directly; this
    scraper geocodes from the postcode instead, matching this
    project's established convention elsewhere, rather than adding a
    coordinate-system conversion dependency for this one platform).
  - Real Decisions layer fields: same shape, but DecDate/Decision/
    DecType instead of Registered — Decision is the REAL, specific
    outcome text (e.g. "Granted", "Refused", "EIA Not Required"),
    genuinely richer than most platforms in this project which only
    expose a binary decided/not-decided signal.
  - Real matching key: Appno appears in BOTH layer types — an
    application that was both received AND decided within the current
    2-week window will appear in both, and should be merged (Decision
    layer's real outcome takes precedence over Applications layer's
    default 'pending').

HONEST LIMITATION: since the web map itself only retains ~2 weeks of
layers at a time, this specific route only ever offers a rolling
2-week window — not a deep historical archive. Edinburgh's own
separate PDF weekly-list pages (edinburgh.gov.uk/downloads/download/
14461/planning-weekly-lists-part-a) go back much further, but require
PDF parsing rather than a clean API — a different, lower-priority
route worth considering separately if deeper history is ever needed.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "City of Edinburgh Council": 328,
}

EDINBURGH_ORG_HOST = "cityofedinburgh.maps.arcgis.com"
EDINBURGH_WEBMAP_ITEM_ID = "af6b177c787b4831b6745ee149cf71fd"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('City of Edinburgh Council','city-of-edinburgh-council','edinburgh_arcgis_api','scotland','https://cityofedinburgh.maps.arcgis.com/apps/webappviewer/index.html?id=0a03789260954f0dbcbc8b124003d91b','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'edinburgh_arcgis_api',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
