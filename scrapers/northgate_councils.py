"""
PlanFind — Northgate PlanningExplorer council configuration.

Deliberately starts with ONE confirmed council, same discipline as
arcus_councils.py's and civica_councils.py's original "starts small"
approach. Runnymede is the only council with real, direct evidence of
working end-to-end (northgate_recon.py + northgate_runnymede_results_
recon.py, 2026-07-24): real homepage form (VIEWSTATE/EVENTVALIDATION —
genuine ASP.NET postback), real fields (#rbRange, #dateStart, #dateEnd,
#csbtnSearch all confirmed by ID), real results table with Status AND
Decision as separate visible columns, real pagination confirmed working.

NOT YET ADDED — investigated today, real but unresolved:
  - Birmingham City Council (eplanning.birmingham.gov.uk): confirmed
    real Northgate user (UK's largest local authority), but its portal
    returned a genuine HTTP 503 on two separate, isolated recon attempts
    with no evidence of a URL migration (unlike Islington below) — looks
    like a real, current outage rather than a stale URL. Worth
    retrying in a future session; also worth considering the PDF
    fallback route given Birmingham separately publishes clean, dated
    weekly PDF lists on their main website.
  - Tamworth Borough Council: confirmed real Northgate user, but its
    portal timed out on two separate, isolated recon attempts with no
    error page at all — genuinely ambiguous, deprioritized given its
    much smaller scale versus Birmingham.
  - London Borough of Islington: CONFIRMED DEAD, not just unconfirmed —
    Islington's own website states directly: "In April 2024, we changed
    our planning application system. If you have saved or bookmarked an
    old link, use the button below and replace your bookmark with this
    link." The Northgate URL genuinely no longer exists; Islington's
    real current vendor hasn't been identified.
"""

NORTHGATE_COUNCILS = [
    ("Runnymede Borough Council", "https://planning.runnymede.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx"),

    # ADDED 2026-08-18 — real URL supplied directly, matches Runnymede's
    # exact GeneralSearch.aspx pattern, so the existing scraper logic
    # should work unmodified (unlike Staffordshire Moorlands, found in
    # the same batch — that one uses a DIFFERENT Northgate sub-variant,
    # ApplicationSearchServlet, matching South Tyneside/Hartlepool/High
    # Peak's still-unbuilt bespoke flow, NOT this GeneralSearch.aspx
    # pattern — deliberately not added here, needs its own real build).
    ("Conwy County Borough Council", "https://npe.conwy.gov.uk/Northgate/EnglishPlanningExplorer/generalsearch.aspx"),

    # RE-ADDED 2026-08-22 — this same council is documented ABOVE in
    # this file's own docstring as previously timing out with no error
    # page on two separate recon attempts, before the UK proxy
    # (introduced ~2026-08-10) existed. That exact signature (a silent
    # hang, no error page at all) matches several other Idox councils
    # this project confirmed were stale, pre-proxy blocks rather than
    # permanent ones once actually re-tested (Tonbridge, Solihull, North
    # East Derbyshire, Bolsover all cleared this way). Worth a genuine
    # retry rather than assuming the old timeout still holds — but
    # given the real prior history, this should get one real, isolated
    # test run before being trusted on the nightly schedule, same
    # discipline as every other re-enabled council.
    ("Tamworth Borough Council", "https://planning.tamworth.gov.uk/northgate/planningexplorer/generalsearch.aspx"),
]

# Hardcoded IDs — set once a real councils-table row exists (see the
# INSERT SQL delivered alongside this file). Runnymede has no existing
# Idox/Arcus/Civica row to reuse — genuinely new to the whole system.
# Conwy and Tamworth likewise genuinely new — no COUNCIL_DB_IDS entry
# yet for either, relies on northgate_scraper.py's live name-match
# fallback until a real id is confirmed and added explicitly (same
# approach as this session's new Idox additions).
COUNCIL_DB_IDS: dict[str, int] = {
    "Runnymede Borough Council": 404,
}
