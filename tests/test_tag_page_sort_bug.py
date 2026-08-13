#!/usr/bin/env python3
"""Regression test for a real bug found and fixed 2026-08-11: every tag
page (Large Sites, Farm Diversification, Commercial Conversion) was
returning a 500 error on EVERY request, regardless of which sort was
chosen. Root cause: SORT_OPTIONS' date_desc/date_asc entries both
reference "an.distance_miles" as a secondary tiebreaker — a table
alias from the applications_near() Postgres function used by the main
postcode search, which the tag-page query never joins against at all.

This is exactly the kind of bug the earlier template-render tests
couldn't catch — they exercised the template with mock data, never the
actual SQL fragment being built. This test checks the real SQL
fragment directly, which is what would have caught it before deploy.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import TAG_SORT_OPTIONS, SORT_OPTIONS


def run():
    checks = []

    # The actual regression check — the real bug, made unmissable
    for key, snippet in TAG_SORT_OPTIONS.items():
        checks.append((f"TAG_SORT_OPTIONS['{key}'] does not reference the "
                        f"missing 'an.' alias",
                        "an." not in snippet))

    # Every possible input a tag page route could pass through .get()
    # must resolve to something safe — real values, None, and anything
    # unrecognized (including "distance", which SORT_OPTIONS has but
    # TAG_SORT_OPTIONS deliberately does not)
    test_inputs = ["date_desc", "date_asc", "distance", None, "", "garbage"]
    for sort_input in test_inputs:
        resolved = TAG_SORT_OPTIONS.get(sort_input, TAG_SORT_OPTIONS["date_desc"])
        checks.append((f"sort={sort_input!r} resolves to something safe "
                        f"(no 'an.' reference)",
                        "an." not in resolved))

    # Confirm TAG_SORT_OPTIONS is genuinely a SEPARATE dict from
    # SORT_OPTIONS, not accidentally the same object — the whole point
    # of the fix
    checks.append(("TAG_SORT_OPTIONS is a distinct dict from SORT_OPTIONS",
                    TAG_SORT_OPTIONS is not SORT_OPTIONS))
    checks.append(("TAG_SORT_OPTIONS deliberately has no 'distance' key "
                    "(no search point on tag pages for it to mean anything)",
                    "distance" not in TAG_SORT_OPTIONS))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
