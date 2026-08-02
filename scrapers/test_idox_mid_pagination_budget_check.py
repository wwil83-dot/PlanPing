#!/usr/bin/env python3
"""Test for the SECOND, real crash fix (2026-08-02) — the first attempt
(2026-08-01) checked between MONTHS, but a real second crash proved
that was the wrong level: with backoff-with-jitter retries now adding
real time to individual page navigations, almost all wall-clock time
for a FAST-mode scrape (typically only 1-2 months) is spent INSIDE a
single month's page-by-page pagination, which the month-level
checkpoint structurally could never reach. This confirms the real
checkpoint now exists in all 3 pagination loops (_scrape_month,
_scrape_month_firstpage_fallback, _scrape_week), not just between
months."""
import sys
sys.path.insert(0, ".")


def run():
    checks = []
    import inspect
    import idox_scraper

    # Confirm all 3 methods that actually paginate page-by-page now
    # accept budget_minutes — this is what makes a real, in-loop check
    # possible at all; without the parameter threaded through, no
    # checkpoint inside these methods could ever fire
    for method_name in ["_scrape_month", "_scrape_month_firstpage_fallback", "_scrape_week"]:
        method = getattr(idox_scraper.IdoxPortal, method_name)
        sig = inspect.signature(method)
        checks.append((f"{method_name} accepts budget_minutes",
                        "budget_minutes" in sig.parameters))

    # Confirm the actual checkpoint text exists in the real pagination
    # loops, not just the (structurally ineffective) month loop —
    # checking the source directly since this needs a real Playwright
    # page/browser to exercise end-to-end
    source = inspect.getsource(idox_scraper)
    mid_pagination_count = source.count("Time budget reached mid-pagination")
    checks.append(("checkpoint present in all 3 real pagination loops",
                    mid_pagination_count == 3))

    mid_council_count = source.count("Time budget reached mid-council")
    checks.append(("original (real but insufficient on its own) month-level "
                    "checkpoint from 2026-08-01 is still there too",
                    mid_council_count == 1))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
