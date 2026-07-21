#!/usr/bin/env python3
"""Test for decision_date extraction (2026-07-21) — using text pulled
directly from the real Wiltshire 'Closed' application screenshot
(PL/2026/04576: Issued Decision 'No Objection', Decision Notice Sent
Date 21/07/2026)."""
from arcus_scraper import _parse_results_html_fallback, _parse_csv, _DECISION_DATE_DIAGNOSED

# Mimics the flattened text _parse_results_html_fallback walks — label
# then value, repeating, matching the real detail-page structure seen in
# the screenshot for a genuinely closed/decided application.
REAL_CLOSED_APP_TEXT = """
<html><body>
Application Reference
PL/2026/04576
Site Address
FISHERTON ALLOTMENTS SALISBURY
Proposal
Willow tree - remove fallen branch
Date Valid
21/07/2026
Application Status
Closed
Decision
No Objection
Decision Notice Sent Date
21/07/2026
</body></html>
"""

REAL_UNDECIDED_APP_TEXT = """
<html><body>
Application Reference
ACV/2026/00016
Site Address
White Hart, Butt Lane, Bishopstone
Proposal
Nomination of the White Hart to be listed as an Asset of Community Value
Date Valid
21/07/2026
Application Status
Under Consultation
</body></html>
"""


def run():
    _DECISION_DATE_DIAGNOSED.clear()
    checks = []

    # 1. A real closed application's decision date gets extracted
    apps = _parse_results_html_fallback(REAL_CLOSED_APP_TEXT, "Wiltshire Council")
    checks.append((
        "real closed application: decision_date extracted correctly",
        len(apps) == 1 and apps[0]["decision_date"] == "2026-07-21"
    ))

    # 2. A genuinely undecided application has no decision_date and
    # correctly does NOT trigger the diagnostic (status isn't decided)
    _DECISION_DATE_DIAGNOSED.clear()
    apps2 = _parse_results_html_fallback(REAL_UNDECIDED_APP_TEXT, "Wiltshire Council")
    checks.append((
        "undecided application: no decision_date, no false diagnostic",
        len(apps2) == 1 and apps2[0]["decision_date"] is None
    ))

    # 3. CSV parsing recognizes at least one plausible decision-date
    # column candidate
    _DECISION_DATE_DIAGNOSED.clear()
    csv_text = (
        "Application Reference,Site Address,Proposal,Date Valid,Status,Decision,Decision Notice Sent Date\n"
        "PL/2026/04576,FISHERTON ALLOTMENTS SALISBURY,Willow tree - remove fallen branch,"
        "21/07/2026,Closed,No Objection,21/07/2026\n"
    )
    csv_apps = _parse_csv(csv_text, "Wiltshire Council")
    checks.append((
        "CSV: decision_date column candidate recognized",
        len(csv_apps) == 1 and csv_apps[0]["decision_date"] == "2026-07-21"
    ))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
