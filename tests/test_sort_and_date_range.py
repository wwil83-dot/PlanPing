#!/usr/bin/env python3
"""Test for user-selectable sorting and explicit date ranges
(2026-08-11).

Imports the real functions from app.main — not duplicated copies —
so this test actually catches a regression if the logic ever changes.

Two things worth testing carefully:
1. Sort resolution must NEVER let an unrecognized/malicious value reach
   the raw SQL string — it has to fall back to a safe default. This is
   the actual security boundary for the whole feature.
2. Date-window widening must never NARROW the window passed to
   applications_near() — only ever leave it the same or widen it,
   otherwise a real date range could silently exclude data the precise
   date_from/date_to check further down was supposed to include.
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import (
    _resolve_sort_order, _widen_days_for_date_range,
    SORT_OPTIONS, DEFAULT_SORT,
)


def run():
    checks = []

    # --- Sort resolution ---
    checks.append(("None sort -> falls back to default",
                    _resolve_sort_order(None) == SORT_OPTIONS[DEFAULT_SORT]))
    checks.append(("empty string sort -> falls back to default",
                    _resolve_sort_order("") == SORT_OPTIONS[DEFAULT_SORT]))
    checks.append(("real 'date_asc' -> its own real SQL snippet",
                    _resolve_sort_order("date_asc") == SORT_OPTIONS["date_asc"]))
    checks.append(("real 'distance' -> its own real SQL snippet",
                    _resolve_sort_order("distance") == SORT_OPTIONS["distance"]))
    # The actual security boundary — an unrecognized value must never
    # reach the query string as-is, only ever resolve to a known-safe
    # hardcoded snippet
    injection_attempt = "date_desc; DROP TABLE planning_applications;--"
    checks.append(("SQL-injection-shaped input -> falls back to default, "
                    "never passed through",
                    _resolve_sort_order(injection_attempt) == SORT_OPTIONS[DEFAULT_SORT]))

    # --- Date-window widening ---
    checks.append(("no date_from -> days passed through unchanged",
                    _widen_days_for_date_range(30, None) == 30))

    # date_from well within the existing 30-day window -> no need to widen
    near_date_from = date.today() - timedelta(days=10)
    checks.append(("date_from inside the existing window -> stays at 30, not narrowed",
                    _widen_days_for_date_range(30, near_date_from) == 30))

    # date_from well OUTSIDE the existing 30-day window -> must widen
    far_date_from = date.today() - timedelta(days=400)
    result = _widen_days_for_date_range(30, far_date_from)
    checks.append((f"date_from 400 days back -> widened to at least 400 (got {result})",
                    result >= 400))

    # Exact boundary case
    exact_date_from = date.today() - timedelta(days=30)
    checks.append(("date_from exactly at the current window boundary -> stays at 30",
                    _widen_days_for_date_range(30, exact_date_from) == 30))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
