"""
PlanFind — Eden and South Lakeland (Westmorland and Furness Council)
config (2026-08-22).

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

# (council_name, base_url)
# NOTE: this scraper covers ONLY the Eden and South Lakeland areas —
# Barrow uses a genuinely separate real system (Oracle APEX-based
# "Barrow Planning Hub", confirmed via barrow_iframe_check.py) needing
# its own dedicated build. Using the real, single official council
# name here deliberately — confirmed directly via planning.data.gov.uk
# ("name": "Westmorland and Furness Council") — rather than a suffixed
# sub-entity name, so a future Barrow scraper can add its own real
# applications to this SAME council_id later, rather than creating a
# second, confusing entry for what is genuinely one real local
# authority.
ESL_COUNCILS = [
    ("Westmorland and Furness Council",
     "https://planningregister.westmorlandandfurness.gov.uk"),
]

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Westmorland and Furness Council','westmorland-and-furness-council','esl_advanced_search','england','https://planningregister.westmorlandandfurness.gov.uk/Search/Advanced','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'esl_advanced_search',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
