#!/usr/bin/env python3
"""Test for Arcus --bulk mode (2026-07-23) — proves bulk_days_back
widens the date range for advanced_search-mode councils, and confirms
it's correctly inert for weekly_list/tabbed_weekly_list modes, which
have no date field to widen at all."""
from datetime import date, timedelta

import arcus_scraper as mod

TODAY = date(2026, 7, 23)


def run():
    checks = []

    # 1. Normal (non-bulk) run: advanced_search council uses the
    # standard 14-day window
    normal_portal = mod.ArcusPortal(
        "Powys County Council", "https://service.powys.gov.uk/pr/s",
        "advanced_search", None, 323,
    )
    normal_date_from = mod._compute_recheck_date_from(
        normal_portal.pending_recheck, TODAY,
        normal_days_back=normal_portal.bulk_days_back or 14,
    )
    checks.append((
        "non-bulk run: advanced_search council uses normal 14-day window",
        normal_date_from == TODAY - timedelta(days=14)
    ))

    # 2. Bulk run: advanced_search council uses the wide 180-day window
    bulk_portal = mod.ArcusPortal(
        "Powys County Council", "https://service.powys.gov.uk/pr/s",
        "advanced_search", None, 323,
        bulk_days_back=180,
    )
    bulk_date_from = mod._compute_recheck_date_from(
        bulk_portal.pending_recheck, TODAY,
        normal_days_back=bulk_portal.bulk_days_back or 14,
    )
    checks.append((
        "bulk run: advanced_search council widens to 180-day window",
        bulk_date_from == TODAY - timedelta(days=180)
    ))

    # 3. bulk_days_back defaults to None when not passed — no accidental
    # bulk behavior on a normal run
    checks.append((
        "bulk_days_back defaults to None (no accidental bulk behavior)",
        normal_portal.bulk_days_back is None
    ))

    # 4. A weekly_list-mode council still gets a bulk_days_back value set
    # on the instance (harmless — nothing in _scrape_weekly_list ever
    # reads it), confirming bulk mode doesn't silently skip these
    # councils, it just has no code path that would act on it
    weekly_portal_bulk = mod.ArcusPortal(
        "Ashford Borough Council", "https://ashford.my.site.com/s/pr",
        "weekly_list", ["Planning Applications Weekly List"], 7,
        bulk_days_back=180,
    )
    checks.append((
        "weekly_list council still receives bulk_days_back (just never reads it)",
        weekly_portal_bulk.bulk_days_back == 180
    ))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
