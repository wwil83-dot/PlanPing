"""
PlanFind — Vale of White Horse & South Oxfordshire Weekly List CSV
scraper config (2026-09-17).

REPLACES the earlier search-form based approach — that hit a genuine
dead end: both councils' own search feature is confirmed broken
(their own site banner says so directly: "We are aware that some
features may not be working as expected"), returning a real,
successful, but permanently empty "Search Results (0)" regardless of
filter combination.

REAL, CONFIRMED alternative: both councils' /Planning/WeeklyList page
offers direct CSV downloads for the last 26 weeks, triggered by a JS
button (button.getWeeklyListCSV, data-date="DD/MM/YYYY") rather than a
plain link — confirmed via vowh_soxon_weeklylist_diagnostic.py. Real,
identical CSV structure on both councils: 3 metadata/title lines, then
a real header row (application_number, received_complete_date,
parish_description, ps2_category, location1, proposal1,
applicants_name, officers_name_1, officers_phone_number1).

HONEST LIMITATION: this is a "Planning Applications Received" report
specifically — it has no decision/status column at all, so every
application from this source is genuinely pending by definition. If
decision data is ever needed, it would require a separate, different
report/mechanism, not yet investigated.

Reuses the SAME real council ids already confirmed and inserted
earlier this session — no new INSERT needed.
"""

VOWH_SOXON_COUNCILS = [
    ("Vale of White Horse District Council", "https://valeofwhitehorse.planning-register.co.uk"),
    ("South Oxfordshire District Council", "https://southoxfordshire.planning-register.co.uk"),
]

COUNCIL_DB_IDS = {
    "Vale of White Horse District Council": 558,
    "South Oxfordshire District Council": 559,
}
