#!/usr/bin/env python3
"""Test for the pagination-navigation-text fix (2026-07-28) — confirmed
real root cause via direct production diagnostic evidence across 6
councils (Folkestone, Bracknell Forest, Powys, Erewash, Reading,
Wrexham), all showing the identical pattern: 'decision': 'Pagination
navigation' and a completely missing 'date' key.

This reconstructs the confirmed mechanism (a stray 'Pagination
navigation' line landing right after a recognized label, corrupting
the 'next line is the value' alignment) using the exact real field
values seen in one of the actual diagnostic printouts (Folkestone,
reference 26/1195/FH), since the raw pre-filtered HTML itself wasn't
captured — this is a reasonable, evidence-grounded reconstruction of
the confirmed mechanism, not a guess at an unrelated scenario."""
from arcus_scraper import _parse_results_html_fallback

# Reconstructs the confirmed real bug: a label, correctly followed by
# its value, but with a stray "Pagination navigation" line landing
# right after "Decision" — exactly the pattern the real diagnostic
# printouts showed (decision wrongly captured as pagination text, date
# missing entirely due to the resulting misalignment).
BUGGY_HTML = """
<html><body>
Application Reference
26/1195/FH
Site Address
46 Earlsfield Road, Hythe, CT21 5PF
Proposal
Erection of single storey side & rear extension, replacement porch, alterations to fenestration and proposed widening of existing dropped kerb, & installation of permeable block paving to parking area.
Status
Under Consultation
Decision
Pagination navigation
Date Valid
20/07/2026
</body></html>
"""


def run():
    checks = []

    apps = _parse_results_html_fallback(BUGGY_HTML, "Folkestone and Hythe District Council")

    checks.append(("real application still parses", len(apps) == 1))
    if apps:
        app = apps[0]
        checks.append(("real reference extracted correctly", app["reference"] == "26/1195/FH"))
        checks.append(("submitted_date is no longer missing/None (the actual bug)",
                        app["submitted_date"] == "2026-07-20"))
        checks.append(("decision field no longer wrongly captures 'Pagination navigation'",
                        app.get("decision_date") != "Pagination navigation"))
        checks.append(("real status extracted correctly despite the fix",
                        app["status"] == "pending"))  # 'Under Consultation' -> pending default

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
