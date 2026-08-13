#!/usr/bin/env python3
"""Test for the real, honest three-state coverage status (2026-08-13) —
Live/Delayed/Offline, computed from data already stored (last_saved_at),
not a new field. Reuses GAP_THRESHOLD_DAYS, the same threshold
/coverage-gaps already uses, so the two pages can't silently disagree
about what "gone quiet" means.

Imports the real function from app.main — not a duplicated copy."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import _coverage_status, GAP_THRESHOLD_DAYS, DELAYED_THRESHOLD_DAYS


def run():
    checks = []

    # Never-covered councils — these should always read as offline,
    # regardless of days_since_save (which is meaningless for them)
    for source in ("pending", "none", "manual_link"):
        result = _coverage_status(source, None)
        checks.append((f"coverage_source='{source}' -> offline",
                        result["key"] == "offline"))

    # A real, active scraper source, genuinely recent — should be live
    result = _coverage_status("idox_scraper", 0)
    checks.append(("saved today -> live", result["key"] == "live"))

    result = _coverage_status("idox_scraper", 1)
    checks.append(("saved yesterday -> live (allows one missed night)",
                    result["key"] == "live"))

    # Real middle state — this is the whole point of the feature, a
    # state neither /councils nor /coverage-gaps could previously show
    result = _coverage_status("idox_scraper", DELAYED_THRESHOLD_DAYS)
    checks.append((f"exactly at the {DELAYED_THRESHOLD_DAYS}-day delayed threshold -> delayed",
                    result["key"] == "delayed"))

    result = _coverage_status("idox_scraper", GAP_THRESHOLD_DAYS - 1)
    checks.append((f"one day before the {GAP_THRESHOLD_DAYS}-day offline threshold -> still delayed",
                    result["key"] == "delayed"))

    # Real offline — matches /coverage-gaps' own existing definition
    # exactly, so the two pages can't disagree
    result = _coverage_status("idox_scraper", GAP_THRESHOLD_DAYS)
    checks.append((f"exactly at the {GAP_THRESHOLD_DAYS}-day threshold -> offline",
                    result["key"] == "offline"))

    result = _coverage_status("idox_scraper", 999)
    checks.append(("genuinely long-dead council (999 days) -> offline",
                    result["key"] == "offline"))

    # A real coverage source that has literally never saved anything —
    # active scraper entry exists, but genuinely nothing has ever come
    # through (distinct from a stale-but-previously-working council)
    result = _coverage_status("idox_scraper", None)
    checks.append(("real scraper source but last_saved_at is NULL -> offline, no crash",
                    result["key"] == "offline"))

    # Every result must always have the fields the template needs — a
    # missing key here would be a real, silent rendering bug
    for source, days in [("idox_scraper", 0), ("pending", None), ("idox_scraper", 999)]:
        result = _coverage_status(source, days)
        checks.append((f"result for ({source!r}, {days!r}) has all required keys",
                        all(k in result for k in ("key", "emoji", "label"))))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
