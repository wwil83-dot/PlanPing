"""
PlanFind — Arcus (Salesforce Built Environment) portal council list.

Unlike Idox, Arcus has no single reusable URL parameter for date-range
searches, and — as of 2026-07-16 — not even a single reusable NAVIGATION
mechanism. Reconnaissance across two days of live investigation found
THREE distinct patterns councils use:

  mode="weekly_list" (most councils so far): homepage has a "Weekly
    lists" or "Quick links" section with clickable links leading straight
    to results. Exact wording is DIFFERENT on every council checked:
      Ashford:        "Planning Applications Weekly List"
      Epping Forest:  "Planning Applications Validated this week" /
                       "Planning Applications Decided this week"
      Manchester:      "Weekly List by Date (7 days)" /
                       "Decision List by Date (7 days)"
      Salford:         "Cases validated in the last 7 days"
      Cumberland:      "Decisions made in the last 7 days" /
                       "Applications Validated past 7 days"
    config = list of exact link texts to click, in order.

  mode="advanced_search" (Bromley, Bracknell Forest, Milton Keynes):
    homepage has NO quick-link shortcut at all — only "Advanced search".
    Uses the sequence proven in recon round 12 (12 rounds of trial and
    error to find): click Advanced search, select "Planning Applications"
    category via Lightning combobox, fill a 14-day date range, click the
    LAST "Search" button on the page (there are usually 2-3 buttons named
    "Search" — the form's real submit is reliably the last one).
    config = None (fixed sequence, no per-council variation needed).

  mode="tabbed_weekly_list" (Eastleigh, Isle of Anglesey): a visibly
    different Arcus template using tabbed navigation (Quick Search /
    Advanced Search / Weekly List as TABS) rather than homepage links.
    Click the "Weekly List" tab; some councils need nothing further
    (Eastleigh — results render immediately), others need a category
    dropdown option selected first (Anglesey).
    config = the exact dropdown option text to select, or None if results
    render directly off the tab click alone.

3. All three modes try "Download as CSV" first (reliable on SOME councils'
   result views, not others — confirmed genuinely inconsistent, not a bug)
   and fall back to parsing the rendered HTML directly if CSV isn't
   offered.

To add a new council: run arcus_recon.py against its register-view URL,
screenshot the real homepage to find which of the three patterns it uses
and the EXACT link/tab/option text involved — guessing does not work, no
two councils checked so far have used identical wording. Then add an
entry below.

Format: (council_name_as_in_supabase_db, base_url, mode, config)

NOTE on mode="advanced_search" and mode="tabbed_weekly_list": these were
built directly from documented recon findings on 2026-07-16 but have NOT
yet run against a live council in production, unlike mode="weekly_list"
which has run successfully for 7 councils across 3 nights. Treat the
first live run using either new mode as the real test.
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
    "Bracknell Forest Council":               17,
    "Milton Keynes City Council":            208,
    "London Borough of Bromley":             230,
    "Eastleigh Borough Council":              301,
    "Isle of Anglesey County Council":       430,
    # Same DB row as idox_councils.py's Powys entry (id 323) — explicit
    # here rather than relying on name-matching fallback, since this is
    # the same council row now scraped by TWO scrapers in parallel
    # (deliberately — see the comment on the tuple entry below).
    "Powys County Council":                  323,
}

ARCUS_COUNCILS = [
    # -------------------------------------------------------------------
    # CONFIRMED WORKING — mode="weekly_list", running successfully across
    # 3 nightly production runs (2026-07-14 through 2026-07-16).
    # -------------------------------------------------------------------
    ("Ashford Borough Council",
     "https://ashfordboroughcouncil.my.site.com/pr/s",
     "weekly_list",
     ["Planning Applications Weekly List"]),

    ("Epping Forest District Council",
     "https://eppingforestdc.my.site.com/pr/s",
     "weekly_list",
     ["Planning Applications Validated this week",
      "Planning Applications Decided this week"]),

    # NOTE: Manchester's custom domain (arcusbe.manchester.gov.uk) has a
    # certificate Chromium rejects by default — the scraper sets
    # ignore_https_errors=True specifically because of this council.
    # WATCH: both links returned 0 results on 2026-07-16 (3rd consecutive
    # empty run for "Decision List by Date"; 1st empty run ever for
    # "Weekly List by Date", which had worked reliably before) — worth a
    # manual check if this continues for a few more nights.
    ("Manchester City Council",
     "https://arcusbe.manchester.gov.uk/pr/s",
     "weekly_list",
     ["Weekly List by Date (7 days)",
      "Decision List by Date (7 days)"]),

    # NOTE: Salford uses "Quick links" rather than "Weekly lists", with
    # the general recent-submissions list called "Cases validated in the
    # last 7 days". A second link, "Current Major Planning Cases", also
    # exists (majors only) but isn't included — same reasoning as not
    # chasing every Idox council's niche sub-lists.
    ("Salford City Council",
     "https://salfordcitycouncil.my.site.com/pr/s",
     "weekly_list",
     ["Cases validated in the last 7 days"]),

    # NOTE: uses the exact same link text as Ashford — "Planning
    # Applications Weekly List" — the first repeat wording seen across
    # councils. Path capitalization is genuinely /PR3/s/ (capital PR3),
    # confirmed correct via real screenshot, not a typo.
    ("Folkestone and Hythe District Council",
     "https://folkestonehythedc.my.site.com/PR3/s",
     "weekly_list",
     ["Planning Applications Weekly List"]),

    ("London Borough of Haringey",
     "https://publicregister.haringey.gov.uk/pr/s",
     "weekly_list",
     ["Planning Applications Validated in last 7 days"]),

    ("Cumberland Council",
     "https://cumberlandcouncil.my.site.com/pr3/s",
     "weekly_list",
     ["Decisions made in the last 7 days",
      "Applications Validated past 7 days"]),

    # -------------------------------------------------------------------
    # mode="advanced_search" — activated 2026-07-16. First live production
    # run using this mode; treat the first scrape as the real test, same
    # way weekly_list mode itself was validated on its debut.
    # -------------------------------------------------------------------
    ("London Borough of Bromley",
     "https://planningaccess.bromley.gov.uk/pr/s",
     "advanced_search", None),  # NOTE: may be replacing an existing
                                # WORKING Idox entry — check whether
                                # that Idox portal has gone stale
                                # before assuming so.
    ("Bracknell Forest Council",
     "https://publicaccess.bracknell-forest.gov.uk/s",
     "advanced_search", None),
    ("Milton Keynes City Council",
     "https://www.be.milton-keynes.gov.uk/pr/s",
     "advanced_search", None),

    # Added 2026-07-21, confirmed via arcus_recon.py real evidence:
    # homepage has no "Weekly lists" heading (same shape as Bromley/
    # Bracknell/Milton Keynes above), Category combobox correctly
    # selected "Planning Applications", and the Advanced Search sanity
    # check reported real results rendered — Playwright got through
    # cleanly with no Cloudflare challenge blocking it, despite
    # en.powys.gov.uk (the SEPARATE general council site, not this one)
    # showing a Cloudflare "Verify you are human" check on an uncached
    # first visit. This IS a genuine live parallel to an existing WORKING
    # Idox entry (council_id 323, "Powys County Council" in
    # idox_councils.py) — deliberately kept running alongside this one
    # rather than retired, per the council's own confirmation that
    # applications submitted before 20 April 2026 remain on the old
    # (Idox) system permanently, while this Arcus register covers
    # everything submitted from 20 April 2026 onwards.
    ("Powys County Council",
     "https://service.powys.gov.uk/pr/s",
     "advanced_search", None),

    # -------------------------------------------------------------------
    # mode="tabbed_weekly_list" — activated 2026-07-16. Same first-live-run
    # caveat as above.
    # -------------------------------------------------------------------
    # NOTE (2026-07-16, after 1st live run failed): base_url corrected to
    # the specific /public-register path — the bare /s/ homepage only
    # shows a generic welcome page with a "Search for Applications" link
    # to click through, NOT the tabs directly. Confirmed from screenshot
    # that /s/public-register is where the real Quick Search / Advanced
    # Search / Weekly List tabs actually live for this council.
    ("Eastleigh Borough Council",
     "https://planning.eastleigh.gov.uk/s/public-register",
     "tabbed_weekly_list", None),  # confirmed via screenshot: results
                                   # render immediately off the tab
                                   # click alone, no category dropdown
                                   # needed.
    ("Isle of Anglesey County Council",
     "https://ioacc.my.site.com/s/pr-english",
     "tabbed_weekly_list", "Planning applications valid this week"),
]

# ---------------------------------------------------------------------------
# SQL to insert the 5 pending councils into Supabase — run once, then
# uncomment the matching entries above and fill in COUNCIL_DB_IDS.
# ---------------------------------------------------------------------------
PENDING_INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('London Borough of Bromley','london-borough-of-bromley','arcus','england','https://planningaccess.bromley.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register','pending',true),
  ('Bracknell Forest Council','bracknell-forest-council','arcus','england','https://publicaccess.bracknell-forest.gov.uk/s/register-view?c__r=Arcus_BE_Public_Register','pending',true),
  ('Milton Keynes City Council','milton-keynes-city-council','arcus','england','https://www.be.milton-keynes.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register','pending',true),
  ('Eastleigh Borough Council','eastleigh-borough-council','arcus','england','https://planning.eastleigh.gov.uk/s/register-view?c__r=Arcus_BE_Public_Register','pending',true),
  ('Isle of Anglesey County Council','isle-of-anglesey-county-council','arcus','wales','https://ioacc.my.site.com/s/pr-english/register-view?c__r=Arcus_BE_Public_Register','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'arcus',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(PENDING_INSERT_SQL)
