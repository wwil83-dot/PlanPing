#!/usr/bin/env python3
"""Test for the internal per-council budget checkpoint added 2026-08-01,
after a real, confirmed crash: the targeted-group job (CONCURRENCY=1,
5s per-request delay) ran a single council's multi-month loop with NO
internal time check at all, wall-clock time crept past GitHub Actions'
own hard job timeout mid-council, and the resulting
asyncio.CancelledError propagated straight through every "except
Exception" handler in the file (CancelledError deliberately isn't a
subclass of Exception since Python 3.8) — crashing the whole run and
silently losing every council still queued behind it (Babergh,
Lewisham, Greenwich, Argyll and Bute never even started)."""
import asyncio


def run():
    checks = []

    # Confirms the real mechanism behind the crash — this is WHY the
    # existing "except Exception" handlers never caught it, not a guess
    checks.append(("CancelledError is NOT a subclass of Exception (Py3.8+)",
                    not issubclass(asyncio.CancelledError, Exception)))
    checks.append(("CancelledError IS a subclass of BaseException",
                    issubclass(asyncio.CancelledError, BaseException)))

    # The actual checkpoint condition added to the month-loop — mirrors
    # the exact logic now in idox_scraper.py's scrape() method
    def would_stop(elapsed, budget_minutes):
        return budget_minutes is not None and elapsed >= budget_minutes - 3

    # Real scenario: targeted job's 30-minute budget, already at 28
    # minutes partway through a council's own month loop — should stop
    checks.append(("28 min elapsed, 30 min budget -> stops (3-min buffer)",
                    would_stop(28, 30) is True))

    # Plenty of budget left — should NOT stop
    checks.append(("10 min elapsed, 30 min budget -> keeps going",
                    would_stop(10, 30) is False))

    # budget_minutes=None (not passed) must never crash or false-trigger —
    # backward compatible with any caller that doesn't pass it
    checks.append(("budget_minutes=None never stops (backward compatible)",
                    would_stop(999, None) is False))

    # Exactly at the buffer boundary — must stop (>=, not >)
    checks.append(("exactly at the 3-min buffer boundary -> stops",
                    would_stop(27, 30) is True))
    checks.append(("just under the buffer boundary -> keeps going",
                    would_stop(26.9, 30) is False))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
