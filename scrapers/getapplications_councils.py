"""
PlanFind — 'getApplications' platform family council configuration.

Format: (council_name_as_in_supabase_db, base_url)

base_url is the real host only (no path/query) — the scraper builds
both the weekly-list POST endpoint and individual detail-page GET URLs
from it. Confirmed real, live, current evidence for all 4 (2026-08-17):
a genuine POST to {base_url}/planning/index.html with body
"fa=getReceivedWeeklyList&week=DD-MM-YYYY" returns real, current
application data with no CAPTCHA and no session/auth required — tested
directly via a live browser Console session, not assumed. See
getapplications_scraper.py's module docstring for the full evidence
trail (WAF behaviour, CAPTCHA finding on the separate Determined list,
etc.) before touching this again.

Real, independent confirmation this is one shared platform, not 4
coincidentally similar sites: a 2022 Place North West article states
Warrington's system directly "resembles the one used by Liverpool City
Council" — and all 4 councils' recon output showed identical page
structure, identical fa=getApplication&id=X detail-link pattern, and
(before the UK-runner fix) an identical WAF block signature.

ADDED 2026-08-17 — a 5th council: London Borough of Waltham Forest.
This closes a real, previously-unsolved mystery from an earlier
session: Waltham Forest's OLD Idox entry was already found and
disabled after being caught silently saving LEWISHAM's real data
mislabeled as Waltham Forest (see idox_councils.py's own detailed
comment trail) — but Waltham Forest itself was left with no working
config at all, pending finding its real portal. It was never an Idox
council to begin with; it's on this platform
(placehub.walthamforest.gov.uk), found directly by the user, not
guessed.

ADDED 2026-08-18 — 4 more councils: Wirral, Cheshire East, Denbighshire,
Stoke-on-Trent. All 4 had existing Idox entries CONFIRMED via direct
SQL to have NEVER actually succeeded (coverage_source still 'pending',
last_saved_at still null, despite being configured for weeks) — not
"already working, leave alone" the way an earlier assumption about 3 of
these initially, wrongly, suggested. Real getApplications URLs supplied
directly by the user. Their existing Idox entries are commented out in
idox_councils.py with a note explaining why, same resolution shape as
Waltham Forest. These 4 REUSE their existing DB rows (real ids already
known — 179, 184, 250, 321) rather than needing a fresh INSERT.
"""


# ---------------------------------------------------------------------------
# Hardcoded correct council IDs from the database.
# Liverpool, Warrington, and Newcastle ALREADY HAVE real DB rows —
# added in an earlier session back when all three were mistakenly
# configured as Idox, before being confirmed broken/moved off-platform
# (see idox_councils.py's own comments: "BROKEN — Liverpool moved off
# Idox to a non-Idox system", same for Warrington). Blackburn with
# Darwen has no existing row — genuinely new.
#
# Run INSERT_SQL below FIRST (safe for all 4 — ON CONFLICT DO UPDATE
# means Liverpool/Warrington/Newcastle's existing rows get their
# portal_url and coverage_source corrected rather than duplicated, and
# Blackburn gets a fresh row), THEN fill in the real ids below via:
#   SELECT id, name FROM councils WHERE name IN (
#     'Liverpool City Council', 'Warrington Borough Council',
#     'Newcastle City Council', 'Blackburn with Darwen Borough Council'
#   );
# ---------------------------------------------------------------------------
COUNCIL_DB_IDS: dict[str, int | None] = {
    "Liverpool City Council":                    51,
    "Warrington Borough Council":                182,
    "Newcastle City Council":                    54,
    "Blackburn with Darwen Borough Council":      469,
    "London Borough of Waltham Forest":          224,
    "Wirral Metropolitan Borough Council":        179,
    "Cheshire East Council":                      184,
    "Denbighshire County Council":                321,
    "Stoke-on-Trent City Council":                250,
    "Nuneaton and Bedworth Borough Council":      None,
    "Coventry City Council":                      None,
    "Breckland Council":                          None,
}

# (council_name_as_in_supabase_db, base_url — host only, no path)
GETAPPLICATIONS_COUNCILS = [
    ("Liverpool City Council",               "https://lar.liverpool.gov.uk"),
    ("Warrington Borough Council",           "https://online.warrington.gov.uk"),
    ("Newcastle City Council",               "https://portal.newcastle.gov.uk"),
    ("Blackburn with Darwen Borough Council", "https://online.blackburn.gov.uk"),
    ("London Borough of Waltham Forest",     "https://placehub.walthamforest.gov.uk"),
    ("Wirral Metropolitan Borough Council",  "https://online.wirral.gov.uk"),
    ("Cheshire East Council",                "https://pa.cheshireeast.gov.uk"),
    ("Denbighshire County Council",          "https://planningandpublicprotection.denbighshire.gov.uk"),
    ("Stoke-on-Trent City Council",          "https://development.stoke.gov.uk"),
    ("Nuneaton and Bedworth Borough Council", "https://idoxcloud.nuneatonandbedworth.gov.uk"),
    ("Coventry City Council",                "https://planandregulatory.coventry.gov.uk"),
    ("Breckland Council",                    "https://publicportal.breckland.gov.uk"),
]


# ---------------------------------------------------------------------------
# Real INSERT SQL — safe to run even for the 3 councils that already
# have a row (ON CONFLICT DO UPDATE corrects portal_url and resets
# coverage_source to 'pending', same "never claim it works before a
# real run confirms it" discipline as every other platform this
# session — East Lothian's stuck-pending bug is exactly what this
# avoids repeating).
# ---------------------------------------------------------------------------
INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Liverpool City Council','liverpool-city-council','getapplications','england','https://lar.liverpool.gov.uk/planning/index.html','pending',true),
  ('Warrington Borough Council','warrington-borough-council','getapplications','england','https://online.warrington.gov.uk/planning/index.html','pending',true),
  ('Newcastle City Council','newcastle-city-council','getapplications','england','https://portal.newcastle.gov.uk/planning/index.html','pending',true),
  ('Blackburn with Darwen Borough Council','blackburn-with-darwen-borough-council','getapplications','england','https://online.blackburn.gov.uk/planning/index.html','pending',true),
  ('London Borough of Waltham Forest','london-borough-of-waltham-forest','getapplications','england','https://placehub.walthamforest.gov.uk/planning/index.html','pending',true),
  ('Wirral Metropolitan Borough Council','wirral-metropolitan-borough-council','getapplications','england','https://online.wirral.gov.uk/planning/index.html','pending',true),
  ('Cheshire East Council','cheshire-east-council','getapplications','england','https://pa.cheshireeast.gov.uk/planning/index.html','pending',true),
  ('Denbighshire County Council','denbighshire-county-council','getapplications','wales','https://planningandpublicprotection.denbighshire.gov.uk/planning/index.html','pending',true),
  ('Stoke-on-Trent City Council','stoke-on-trent-city-council','getapplications','england','https://development.stoke.gov.uk/planning/index.html','pending',true),
  ('Nuneaton and Bedworth Borough Council','nuneaton-and-bedworth-borough-council','getapplications','england','https://idoxcloud.nuneatonandbedworth.gov.uk/planning/index.html','pending',true),
  ('Coventry City Council','coventry-city-council','getapplications','england','https://planandregulatory.coventry.gov.uk/planning/index.html','pending',true),
  ('Breckland Council','breckland-council','getapplications','england','https://publicportal.breckland.gov.uk/planning/index.html','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'getapplications',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
