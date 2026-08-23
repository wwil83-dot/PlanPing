"""
PlanFind — "Search/Advanced" platform family config (2026-08-22,
extended 2026-08-23).

4 councils sharing one real platform: Westmorland and Furness Council
(Eden/South Lakeland areas only — Barrow is separate), Cherwell,
Wychavon, Malvern Hills.

Real, confirmed evidence backing every design decision here — full
recon trail across 6 rounds: wandf_recon.py, wandf_recon_round2.py,
wandf_recon_round3.py, esl_pagination_test.py, esl_request_inspection.py,
esl_next_link_test.py, esl_ajax_pagination_test.py, esl_real_click_test.py.

REAL, CONFIRMED (not guessed):
  - Real URL: planningregister.westmorlandandfurness.gov.uk/Search/Advanced
  - Real date-range fields: #DateReceivedFrom, #DateReceivedTo, plain
    text inputs (DD/MM/YYYY), NOT readonly — a direct .fill() works,
    no JS-workaround needed unlike Northgate's readonly fields.
  - Real search button: a plain <button> containing the text "Search".
  - Real results land at a STABLE, reusable URL every time —
    /Search/Results — genuinely different from South Tyneside's
    one-time dynamic URL. Session-state-based: the real Results page
    itself carries no query parameters at all: the search criteria is
    stored server-side after the initial POST, not encoded in the URL.
  - Real results table: 4 columns — Application Number | Location |
    Proposal | Status. Every cell contains a real, hidden accessibility
    label span (class="mobile-heading", e.g. "Application No.",
    "Location") BEFORE the real content — confirmed directly via the
    actual captured markup. Must be stripped before extracting real
    text, same discipline as South Tyneside's "View more details for"
    fix earlier this project — otherwise labels bleed into every field.
  - Real "Status" column is a WORKFLOW STAGE (confirmed real values:
    "Valid", "Consultation Started"), NOT a final decision outcome —
    same discipline as every other platform here. No separate
    "Decision" column exists in the search results list itself
    (the Advanced Search FORM does have DateIssuedFrom/To fields,
    suggesting decisions are tracked, just not surfaced in this list)
    — matches Hartlepool's situation in the Northgate servlet family:
    every application starts 'pending', a recheck pass against the
    real, stable detail-page URL is the only route to a real decision.
  - Real, permanent, non-session-bound detail URL: a real reference
    plugged directly into /Planning/Display/{reference} — e.g.
    /Planning/Display/2026/1595/FPA — confirmed reusable directly,
    genuinely simpler than South Tyneside's PKID-in-temp-file situation.
  - REAL, CONFIRMED PAGINATION (this took 6 full recon rounds to nail
    down — worth remembering for next time a similar site appears):
    a "Next" link exists with an EMPTY href and a
    data-ajax-target="/Search/ResultsPage/{N}?module=PLA" attribute — a
    jQuery Unobtrusive AJAX pattern. Manually reconstructing and
    fetching that URL directly FAILED both via plain navigation (200
    but empty) and via a manually-replicated AJAX header (404) — the
    ONLY confirmed working approach is a REAL click on the live "Next"
    element in an already-loaded results page, letting the real
    client-side JS handler make its own correctly-authenticated AJAX
    call. Confirmed directly: real network capture showed
    GET /Search/ResultsPage/2?module=PLA returning 10 genuinely new,
    zero-overlap references after a real click.
  - Real total-count text confirmed: "(114)" appears directly in the
    page, giving a real, reliable target to know when to stop clicking
    Next.
  - CONFIRMED (2026-08-22, real web research): this council's
    /Search/Advanced URL path exactly matches Cherwell, North
    Warwickshire, Wychavon, and Malvern Hills from an earlier council-
    search batch — strong evidence this is a shared platform, not a
    unique one-off. Worth investigating those 4 directly using this
    same real, hard-won evidence as a starting point before assuming
    they need their own from-scratch recon.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Westmorland and Furness Council": 513,
}

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Westmorland and Furness Council":  513,
    "Cherwell District Council":        514,
    "Wychavon District Council":        515,
    "Malvern Hills District Council":   516,
    "North Warwickshire Borough Council": 517,
}

# (council_name, base_url)
# NOTE: this scraper covers ONLY the Eden and South Lakeland areas of
# Westmorland and Furness — Barrow uses a genuinely separate real
# system (Oracle APEX-based "Barrow Planning Hub", confirmed via
# barrow_iframe_check.py) needing its own dedicated build.
#
# ADDED 2026-08-23 — Cherwell, Wychavon, Malvern Hills confirmed as
# genuinely the SAME shared platform, not just a coincidental URL
# match. Real, direct confirmation via search_advanced_family_recon.py:
# identical field ids, identical /Search/Results landing URL, Wychavon
# and Malvern Hills' table headers matching Eden/South Lakeland's
# byte-for-byte, and the identical data-ajax-target="/Search/
# ResultsPage/{N}?module=PLA" pagination pattern already solved for
# Eden/South Lakeland. ONE real, confirmed difference: these 3 require
# the top-level "Planning" checkbox (#SearchPlanning) to be explicitly
# checked before a search will process — confirmed via direct
# screenshot evidence that an unchecked submission gets silently
# rejected, just re-serving a blank form. Eden/South Lakeland's own
# search worked without ever checking this box, but checking it
# anyway for all councils uniformly is harmless and makes the whole
# family more robust and consistent.
#
# North Warwickshire Borough Council deliberately NOT included here —
# confirmed via the same recon to redirect through a real disclaimer-
# acceptance page first, a genuinely different flow needing its own
# dedicated handling before it can be added safely.
ESL_COUNCILS = [
    ("Westmorland and Furness Council",
     "https://planningregister.westmorlandandfurness.gov.uk"),
    ("Cherwell District Council",
     "https://planningregister.cherwell.gov.uk"),
    ("Wychavon District Council",
     "https://plan.wychavon.gov.uk"),
    ("Malvern Hills District Council",
     "https://plan.malvernhills.gov.uk"),
    # ADDED 2026-08-23 — genuinely the same shared platform (confirmed:
    # same field ids, same #SearchPlanning checkbox requirement, same
    # data-ajax-target AJAX pagination mechanism) but running a newer,
    # still-being-migrated front-end template (the site's own real
    # banner text: "Welcome to the new planning register... some
    # features may not be working as expected"). THREE real, confirmed
    # differences the scraper handles defensively, not via a hard
    # per-council branch, in case future councils share any of these
    # variations too:
    #   1. A real disclaimer-acceptance gate before /Search/Advanced
    #      becomes accessible — confirmed via nwarks_disclaimer_recon.py,
    #      a plain "Accept" button, no checkbox involved.
    #   2. #DateReceivedFrom/To are genuine HTML5 type="date" inputs
    #      here (every other council uses plain type="text") — real,
    #      hidden behind a JS date-picker library, confirmed needing a
    #      JS-direct value set (ISO format) + dispatched change/input
    #      events, same category of fix as Northgate's readonly fields
    #      elsewhere in this project.
    #   3. Real results render as styled "cards" (div.searchResultsCardRow)
    #      instead of a <table> — confirmed real structure: a real,
    #      permanent detail URL (/Planning/Display?applicationNumber=
    #      {ref}), reference in an <h2>, description in a separate div.
    #      Real pagination uses the SAME data-ajax-target mechanism,
    #      just as an icon-based chevron-right control (with a
    #      genuinely convenient real data-total-pages attribute) rather
    #      than the plain "Next" text link the other 4 use.
    ("North Warwickshire Borough Council",
     "https://planning.northwarks.gov.uk"),
]

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Westmorland and Furness Council','westmorland-and-furness-council','esl_advanced_search','england','https://planningregister.westmorlandandfurness.gov.uk/Search/Advanced','pending',true),
  ('Cherwell District Council','cherwell-district-council','esl_advanced_search','england','https://planningregister.cherwell.gov.uk/Search/Advanced','pending',true),
  ('Wychavon District Council','wychavon-district-council','esl_advanced_search','england','https://plan.wychavon.gov.uk/Search/Advanced','pending',true),
  ('Malvern Hills District Council','malvern-hills-district-council','esl_advanced_search','england','https://plan.malvernhills.gov.uk/Search/Advanced','pending',true),
  ('North Warwickshire Borough Council','north-warwickshire-borough-council','esl_advanced_search','england','https://planning.northwarks.gov.uk/Search/Advanced','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'esl_advanced_search',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
