"""
PlanFind — Arcus (Salesforce Built Environment) portal council list.

Unlike Idox, Arcus has no single reusable URL parameter for date-range
searches. Reconnaissance across 12 rounds (2026-07-13) proved the
reliable, portable mechanism is:

  1. Navigate to {base_url}/register-view?c__r=Arcus_BE_Public_Register
  2. Click the council's own "Weekly lists" link — CONFIRMED to be worded
     differently on every council checked so far:
       Ashford:        "Planning Applications Weekly List"
       Epping Forest:  "Planning Applications Validated this week" /
                        "Planning Applications Decided this week"
       Manchester:      "Weekly List by Date (7 days)" /
                        "Decision List by Date (7 days)"
     This is why link text is a PER-COUNCIL config field below, not a
     shared constant — there is no universal wording, only a universal
     mechanism (a "Weekly lists" section always seems to exist).
  3. Attempt "Download as CSV" — confirmed reliable on weekly-list result
     views specifically (NOT reliably present on Advanced Search results,
     which is why the scraper uses the weekly-list route, not Advanced
     Search, despite Advanced Search being what recon rounds 8-12 actually
     validated end-to-end). If CSV isn't offered for a given council, the
     scraper falls back to parsing the rendered HTML directly.

To add a new council: run arcus_recon.py against its register-view URL,
screenshot the real homepage to find the EXACT "Weekly lists" section link
text (guessing does not work — 3 different councils, 3 different exact
wordings), then add an entry below.

Format: (council_name_as_in_supabase_db, base_url, [link_texts_to_try])

A council can list MORE THAN ONE link text — e.g. Epping Forest has both a
"Validated this week" and a "Decided this week" list. The scraper runs
each configured link in turn and upserts everything found; duplicate
references across the two lists collapse naturally via the existing
council_id+reference upsert conflict key, same as Idox's dual-month fetch.
"""

# ---------------------------------------------------------------------------
# Hardcoded correct council IDs from the database. Same pattern as
# idox_councils.py's COUNCIL_DB_IDS — bypasses unreliable name-matching.
# To add a new council: query Supabase for its id with:
#   SELECT id, name FROM councils WHERE name = 'Council Name';
# ---------------------------------------------------------------------------
COUNCIL_DB_IDS: dict[str, int] = {
    "Ashford Borough Council":               7,
    "Epping Forest District Council":       35,
    "Manchester City Council":              52,
}

ARCUS_COUNCILS = [
    # -------------------------------------------------------------------
    # CONFIRMED WORKING — validated end-to-end in recon round 12
    # (category selected, dates filled and verified, correct Search
    # button identified, real results rendered, CSV downloaded for at
    # least Ashford).
    # -------------------------------------------------------------------
    ("Ashford Borough Council",
     "https://ashfordboroughcouncil.my.site.com/pr/s",
     ["Planning Applications Weekly List"]),

    ("Epping Forest District Council",
     "https://eppingforestdc.my.site.com/pr/s",
     ["Planning Applications Validated this week",
      "Planning Applications Decided this week"]),

    # NOTE: Manchester's custom domain (arcusbe.manchester.gov.uk) has a
    # certificate Chromium rejects by default — the scraper sets
    # ignore_https_errors=True specifically because of this council.
    ("Manchester City Council",
     "https://arcusbe.manchester.gov.uk/pr/s",
     ["Weekly List by Date (7 days)",
      "Decision List by Date (7 days)"]),

    # -------------------------------------------------------------------
    # KNOWN ARCUS COUNCILS — NOT YET RECONNOITRED
    # These were confirmed to be running Arcus during earlier Idox-hunting
    # sessions (found the platform, not the exact link text), but nobody's
    # captured a real screenshot of their homepage yet to confirm the
    # actual "Weekly lists" wording. Do NOT guess — run arcus_recon.py
    # against each and get a real screenshot first, same process that
    # worked for the three above.
    # -------------------------------------------------------------------
    # ("Salford City Council",
    #  "https://salfordcitycouncil.my.site.com/pr/s", []),
    # ("Bracknell Forest Council",
    #  "https://publicaccess.bracknell-forest.gov.uk/s", []),
    # ("Milton Keynes City Council",
    #  "https://www.be.milton-keynes.gov.uk/pr/s", []),
    # ("Isle of Anglesey County Council",
    #  "https://ioacc.my.site.com/s", []),  # NOTE: different path shape
    #                                        # (/s/pr-english, not /pr/s) —
    #                                        # confirm before adding.
]

# ---------------------------------------------------------------------------
# SQL to insert Arcus councils into Supabase — run once before the first
# scrape, same pattern as Idox's INSERT_SQL.
# ---------------------------------------------------------------------------
INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Ashford Borough Council','ashford-borough-council','arcus','england','https://ashfordboroughcouncil.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register','pending',true),
  ('Epping Forest District Council','epping-forest-district-council','arcus','england','https://eppingforestdc.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register','pending',true),
  ('Manchester City Council','manchester-city-council','arcus','england','https://arcusbe.manchester.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'arcus',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
