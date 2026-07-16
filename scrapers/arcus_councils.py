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
    "Salford City Council":                 62,
    "Folkestone and Hythe District Council": 423,
    "London Borough of Haringey":            243,
    "Cumberland Council":                    424,
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

    # NOTE: Salford uses a different homepage section wording again —
    # "Quick links" rather than "Weekly lists" — with the general recent-
    # submissions list called "Cases validated in the last 7 days". A
    # second link, "Current Major Planning Cases", also exists (majors
    # only) but isn't included here — same reasoning as not chasing every
    # Idox council's niche sub-lists, the general list already captures
    # everything that matters for a nightly scrape.
    ("Salford City Council",
     "https://salfordcitycouncil.my.site.com/pr/s",
     ["Cases validated in the last 7 days"]),

    # NOTE: uses the exact same link text as Ashford — "Planning
    # Applications Weekly List" — the first repeat wording seen across 5
    # councils. Path capitalization is genuinely /PR3/s/ (capital PR3),
    # confirmed correct via real screenshot, not a typo.
    ("Folkestone and Hythe District Council",
     "https://folkestonehythedc.my.site.com/PR3/s",
     ["Planning Applications Weekly List"]),

    ("London Borough of Haringey",
     "https://publicregister.haringey.gov.uk/pr/s",
     ["Planning Applications Validated in last 7 days"]),

    ("Cumberland Council",
     "https://cumberlandcouncil.my.site.com/pr3/s",
     ["Decisions made in the last 7 days",
      "Applications Validated past 7 days"]),

    # -------------------------------------------------------------------
    # KNOWN ARCUS COUNCILS — CONFIRMED BUT NOT YET SCRAPABLE
    # Reconnoitred 2026-07-14. All three genuinely have NO "Weekly lists"
    # or "Quick links" section at all on their homepage — unlike every
    # other council above, which all had SOME quick-link shortcut, just
    # worded differently. Only "Advanced search" is available.
    # arcus_scraper.py currently only knows how to click a weekly-list
    # link — it has no Advanced Search code path (category selection,
    # date filling, click the LAST Search button — see round 12) even
    # though that mechanism was fully proven working in recon. Adding
    # these requires building that second code path into the scraper
    # first; do not attempt to add them as plain weekly-list entries,
    # there is no such link to click.
    # -------------------------------------------------------------------
    # ("Bracknell Forest Council",
    #  "https://publicaccess.bracknell-forest.gov.uk/s", []),
    # ("Milton Keynes City Council",
    #  "https://www.be.milton-keynes.gov.uk/pr/s", []),
    # ("London Borough of Bromley",
    #  "https://planningaccess.bromley.gov.uk/pr/s", []),  # NOTE: may be
    #                                        # replacing an existing WORKING
    #                                        # Idox entry — check whether
    #                                        # that Idox portal has gone
    #                                        # stale before assuming so.

    # -------------------------------------------------------------------
    # KNOWN ARCUS COUNCILS — CONFIRMED LIVE, DIFFERENT UI TEMPLATE
    # Reconnoitred 2026-07-14. Both use a visibly different Arcus template
    # — tabbed navigation (Quick Search / Advanced Search / Weekly List as
    # tabs, not homepage links) rather than the "Weekly lists" section
    # every other council above has. Eastleigh in particular renders
    # results immediately with no Lightning-loading delay, unlike every
    # other council. Real, working, worth adding — but the current click
    # sequence in arcus_scraper.py was built against the OTHER template
    # and will likely need its own dedicated logic, not just a new link
    # text. Needs its own testing before being added as an active entry.
    # -------------------------------------------------------------------
    # ("Eastleigh Borough Council",
    #  "https://planning.eastleigh.gov.uk/s", []),  # weekly list tab
    #                                        # confirmed present, real
    #                                        # results render without delay
    # ("Isle of Anglesey County Council",
    #  "https://ioacc.my.site.com/s/pr-english", []),  # NOTE: confirmed
    #                                        # correct path shape now —
    #                                        # /s/pr-english, tabbed UI,
    #                                        # "Planning applications valid
    #                                        # this week" dropdown option
    #                                        # under the Weekly List tab.
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
