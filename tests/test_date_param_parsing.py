#!/usr/bin/env python3
"""Regression test for a real production bug found 2026-08-13: EVERY
filter submission on the postcode search, and every tag page, was
returning a 422 error whenever the date_from/date_to fields were left
blank — which is the normal case for anyone not specifically using a
date range. Root cause: FastAPI validates Optional[date] parameters
BEFORE the route function body runs at all, and rejects an empty
string as "not a valid date" rather than treating it as an absent
parameter — the exact same empty-string-vs-None lesson already learned
for status/app_type/keyword, but at a layer where our own code never
even got a chance to normalize it first.

Imports the real function from app.main — not a duplicated copy."""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import _parse_date_param


def run():
    checks = []

    # The actual regression — this exact input is what a real, blank
    # date field submits, and is what was crashing every filter request
    checks.append(("empty string '' -> None (the real production bug)",
                    _parse_date_param("") is None))
    checks.append(("None input -> None",
                    _parse_date_param(None) is None))
    checks.append(("whitespace-only '   ' -> None",
                    _parse_date_param("   ") is None))

    # Real, valid dates
    checks.append(("real ISO date '2024-01-15' -> correct date object",
                    _parse_date_param("2024-01-15") == date(2024, 1, 15)))
    checks.append(("real ISO date with surrounding whitespace -> stripped and parsed",
                    _parse_date_param("  2023-12-31  ") == date(2023, 12, 31)))

    # Malformed input must not crash the whole page — treated as "no
    # filter" rather than propagating an exception
    checks.append(("garbage input 'not-a-date' -> None, doesn't raise",
                    _parse_date_param("not-a-date") is None))
    checks.append(("malformed but date-shaped '2024-13-45' -> None, doesn't raise",
                    _parse_date_param("2024-13-45") is None))
    checks.append(("wrong format '15/01/2024' -> None, doesn't raise",
                    _parse_date_param("15/01/2024") is None))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
