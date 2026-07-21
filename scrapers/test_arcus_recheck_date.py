#!/usr/bin/env python3
"""Test for _compute_recheck_date_from (2026-07-21) — the Arcus
decision-cadence date-widening logic, used by _scrape_advanced_search
for the 3 councils (Bromley, Bracknell Forest, Milton Keynes) where
widening is actually possible."""
from datetime import date, timedelta

from arcus_scraper import _compute_recheck_date_from

TODAY = date(2026, 7, 21)


def run():
    checks = []

    # 1. No pending recheck at all -> stays at the normal 14-day window
    result = _compute_recheck_date_from([], TODAY)
    checks.append(("empty pending list -> normal 14-day window",
                    result == TODAY - timedelta(days=14)))

    # 2. A pending application 40 days old -> widens back to cover it
    pending = [{"reference": "X", "submitted_date": (TODAY - timedelta(days=40)).isoformat()}]
    result = _compute_recheck_date_from(pending, TODAY)
    checks.append(("40-day-old pending app -> widens to 40 days back",
                    result == TODAY - timedelta(days=40)))

    # 3. A pending application only 5 days old -> does NOT narrow the
    # window below the normal 14 days (5 days is already inside it)
    pending = [{"reference": "X", "submitted_date": (TODAY - timedelta(days=5)).isoformat()}]
    result = _compute_recheck_date_from(pending, TODAY)
    checks.append(("5-day-old pending app (already in window) -> stays at 14 days",
                    result == TODAY - timedelta(days=14)))

    # 4. A pending application 300 days old -> capped at the 120-day
    # floor, not widened indefinitely
    pending = [{"reference": "X", "submitted_date": (TODAY - timedelta(days=300)).isoformat()}]
    result = _compute_recheck_date_from(pending, TODAY)
    checks.append(("300-day-old pending app -> capped at 120-day floor",
                    result == TODAY - timedelta(days=120)))

    # 5. Multiple pending apps -> widens to the OLDEST one, not an average
    pending = [
        {"reference": "A", "submitted_date": (TODAY - timedelta(days=20)).isoformat()},
        {"reference": "B", "submitted_date": (TODAY - timedelta(days=60)).isoformat()},
        {"reference": "C", "submitted_date": (TODAY - timedelta(days=30)).isoformat()},
    ]
    result = _compute_recheck_date_from(pending, TODAY)
    checks.append(("multiple pending apps -> widens to the OLDEST (60 days)",
                    result == TODAY - timedelta(days=60)))

    # 6. Malformed/missing submitted_date entries are skipped, not fatal
    pending = [
        {"reference": "A", "submitted_date": None},
        {"reference": "B", "submitted_date": "not-a-date"},
        {"reference": "C", "submitted_date": (TODAY - timedelta(days=25)).isoformat()},
    ]
    result = _compute_recheck_date_from(pending, TODAY)
    checks.append(("malformed entries skipped, real one (25 days) still used",
                    result == TODAY - timedelta(days=25)))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
