"""
PlanFind — Northern Ireland shared Planning Portal council configuration.

Format: (council_name_as_in_supabase_db, authority_id)

authority_id is the REAL, CONFIRMED value from a direct API call to
https://api-planningregister-planningportal.pr.tqinfra.co.uk/api/v1/authorities
(2026-08-17) — not guessed, not scraped from dropdown HTML text. Pulled
straight from the JSON response, same "get real evidence" discipline as
every other platform's council list.

One shared portal covers 10 of NI's 11 councils. Mid Ulster is the sole
exception — it already has its own standard Idox entry in
idox_councils.py (confirmed working, added 2026-08 in the East Ayrshire/
Aberdeenshire batch) and does NOT belong here.

AuthorityId=11 ("Department for Infrastructure (DfI) - Strategic
Planning Division") is DELIBERATELY EXCLUDED below. It's a real entry
in the API's authority list, but it's NI's regional planning body for
major/regionally-significant applications (motorways, energy
infrastructure, etc.) — not a district council. Including it would
misrepresent "10 NI councils" as 11, and it's a fundamentally different
kind of applicant/decision-maker than the local-authority data this
site otherwise presents. Worth a real product decision later (it's
genuinely public, useful planning data) but not silently folded into
council coverage counts. To add it: uncomment the line below AND treat
it distinctly in the UI (e.g. not on the "councils covered" count),
don't just flip it on.
"""

# ---------------------------------------------------------------------------
# Hardcoded correct council IDs from the database.
# These are PLACEHOLDERS — none of these 10 councils exist in the
# councils table yet (genuinely new, unlike most other platform
# additions which reuse an existing row). Run the INSERT SQL delivered
# alongside this file FIRST, then replace every None below with the
# real id Supabase assigns, using:
#   SELECT id, name FROM councils WHERE name = 'Council Name';
# The scraper will refuse to run for any council still mapped to None
# rather than silently skipping it — see ni_scraper.py's startup check.
# ---------------------------------------------------------------------------
COUNCIL_DB_IDS: dict[str, int | None] = {
    "Antrim and Newtownabbey Borough Council":              None,
    "Ards and North Down Borough Council":                  None,
    "Armagh City, Banbridge and Craigavon Borough Council": None,
    "Belfast City Council":                                 None,
    "Causeway Coast and Glens Borough Council":              None,
    "Derry City and Strabane District Council":              None,
    "Fermanagh and Omagh District Council":                  None,
    "Lisburn and Castlereagh City Council":                  None,
    "Mid and East Antrim Borough Council":                   None,
    "Newry, Mourne and Down District Council":               None,
}

# (council_name_as_in_supabase_db, real confirmed authority_id)
NI_COUNCILS = [
    ("Antrim and Newtownabbey Borough Council",              1),
    ("Ards and North Down Borough Council",                  2),
    ("Armagh City, Banbridge and Craigavon Borough Council", 3),
    ("Belfast City Council",                                 4),
    ("Causeway Coast and Glens Borough Council",              5),
    ("Derry City and Strabane District Council",              6),
    ("Fermanagh and Omagh District Council",                  7),
    ("Lisburn and Castlereagh City Council",                  8),
    ("Mid and East Antrim Borough Council",                   9),
    ("Newry, Mourne and Down District Council",               10),

    # DELIBERATELY EXCLUDED — see module docstring.
    # ("Department for Infrastructure (DfI) - Strategic Planning Division", 11),
]


# ---------------------------------------------------------------------------
# Real INSERT SQL for the 10 new council rows — run this in Supabase
# BEFORE the scraper's first real run. Matches the same shape as every
# other platform's insert (idox_councils.py's INSERT_SQL block etc.):
# system/coverage_source starts at 'pending' deliberately (same lesson
# learned from East Lothian this session — never pre-set 'ni_scraper'
# here before a real run has actually confirmed it works; the scraper
# itself flips coverage_source once it successfully saves data).
# ---------------------------------------------------------------------------
INSERT_SQL = """
INSERT INTO councils (name, slug, system, country, portal_url, coverage_source, active)
VALUES
  ('Antrim and Newtownabbey Borough Council','antrim-and-newtownabbey-borough-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Ards and North Down Borough Council','ards-and-north-down-borough-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Armagh City, Banbridge and Craigavon Borough Council','armagh-city-banbridge-and-craigavon-borough-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Belfast City Council','belfast-city-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Causeway Coast and Glens Borough Council','causeway-coast-and-glens-borough-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Derry City and Strabane District Council','derry-city-and-strabane-district-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Fermanagh and Omagh District Council','fermanagh-and-omagh-district-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Lisburn and Castlereagh City Council','lisburn-and-castlereagh-city-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Mid and East Antrim Borough Council','mid-and-east-antrim-borough-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true),
  ('Newry, Mourne and Down District Council','newry-mourne-and-down-district-council','ni_planning_portal','northern_ireland','https://planningregister.planningsystemni.gov.uk/list-search','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'ni_planning_portal',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
