#!/usr/bin/env python3
"""Regression test for a real, confirmed bug found 2026-08-13: Camden
(coverage_source='data_gov_uk', fed by the national open-data
harvester, not any of the Idox/Arcus/Civica/Northgate scrapers) had
last_saved_at = NULL despite 64,572 real applications with the freshest
dated literally yesterday — falsely showing as "Offline" on both
/councils and the council detail page, the opposite of what an honest
status feature should ever do.

Imports the real function from app.main — not a duplicated copy."""
import sys
import os
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import _effective_days_since_save, _coverage_status


class FakeTimestamp:
    """Mimics asyncpg's real datetime return type closely enough for
    this test — only needs .date()."""
    def __init__(self, dt):
        self._dt = dt

    def date(self):
        return self._dt.date()


def run():
    checks = []

    # --- The actual real-world regression: Camden's exact scenario ---
    yesterday = date.today() - timedelta(days=1)
    result = _effective_days_since_save(None, yesterday)
    checks.append((f"Camden's real scenario (last_saved_at=NULL, freshest "
                    f"application=yesterday) -> 1, not None",
                    result == 1))

    status = _coverage_status("data_gov_uk", result)
    checks.append(("Camden's real scenario resolves to Live, not Offline",
                    status["key"] == "live"))

    # --- Normal case: last_saved_at present and real, takes priority ---
    real_timestamp = FakeTimestamp(datetime.combine(date.today(), datetime.min.time()))
    result2 = _effective_days_since_save(real_timestamp, date.today() - timedelta(days=50))
    checks.append(("real last_saved_at present -> uses IT, ignores the fallback "
                    "even though the fallback is very different",
                    result2 == 0))

    # --- Both genuinely absent — the honest "we don't know" case ---
    checks.append(("both last_saved_at and fallback are None -> None (genuinely unknown)",
                    _effective_days_since_save(None, None) is None))

    # --- Fallback itself is old — a council with genuinely stale data
    # and no last_saved_at should still show as stale, not falsely live
    old_date = date.today() - timedelta(days=30)
    result3 = _effective_days_since_save(None, old_date)
    checks.append((f"fallback is itself 30 days old -> stays 30, not "
                    f"incorrectly treated as fresh (got {result3})",
                    result3 == 30))
    status3 = _coverage_status("data_gov_uk", result3)
    checks.append(("a genuinely stale fallback still resolves to Offline, "
                    "not falsely Live",
                    status3["key"] == "offline"))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
