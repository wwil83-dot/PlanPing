"""
PlanFind — Civica Portal360 council configuration.

Deliberately starts with ONE confirmed council, same discipline as
arcus_councils.py's original "starts small (3 confirmed councils)"
approach. St Albans is the only council with real, direct evidence of
working end-to-end (civica_recon.py + civica_stalbans_results_recon.py,
2026-07-23/24): real homepage, real weekly-list links, real results
rendering from a direct URL with no form-click needed, real references/
addresses/proposals confirmed in the actual HTML.

REJECTED — Waverley Borough Council (also a real Portal360 council):
confirmed BLOCKED by a genuine Incapsula WAF (real HTML showed a literal
"Request unsuccessful. Incapsula incident ID: ..." block page, and the
screenshot showed Imperva's "Access denied — Error 16" page directly).
Not something to build around — same category as Tonbridge/Solihull/
Bolsover on the Idox side. Its manual_link portal_url was separately
fixed (was missing the :4443 port number) so real human visitors get a
working link, even though automated scraping is genuinely blocked.

West Northamptonshire Council — investigated as a possible Civica
candidate, real register confirmed live, but every recon attempt landed
on a "Copyright & Disclaimer" interstitial with no confirmed way through
it (a generic "Accept" click did nothing). Deprioritized in favor of
St Albans, not added here.
"""

CIVICA_COUNCILS = [
    ("St Albans", "https://planningapplications.stalbans.gov.uk/planning"),
]

# Hardcoded IDs — set once a real councils-table row exists (see the
# INSERT SQL delivered alongside this file). No existing Idox/Arcus row
# to reuse for St Albans, unlike Powys/Reading/Erewash/Wrexham — this is
# a genuinely brand-new council to the whole system.
COUNCIL_DB_IDS: dict[str, int] = {
    "St Albans": 440,
}
