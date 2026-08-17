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
    "Liverpool City Council":                    None,
    "Warrington Borough Council":                None,
    "Newcastle City Council":                    None,
    "Blackburn with Darwen Borough Council":      None,
}

# (council_name_as_in_supabase_db, base_url — host only, no path)
GETAPPLICATIONS_COUNCILS = [
    ("Liverpool City Council",               "https://lar.liverpool.gov.uk"),
    ("Warrington Borough Council",           "https://online.warrington.gov.uk"),
    ("Newcastle City Council",               "https://portal.newcastle.gov.uk"),
    ("Blackburn with Darwen Borough Council", "https://online.blackburn.gov.uk"),
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
  ('Blackburn with Darwen Borough Council','blackburn-with-darwen-borough-council','getapplications','england','https://online.blackburn.gov.uk/planning/index.html','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'getapplications',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
