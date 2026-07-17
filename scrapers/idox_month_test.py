#!/usr/bin/env python3
"""
PlanFind — Idox month-selection diagnostic test.

PURPOSE: the --bulk mode fix (select the dropdown option matching the
actually-requested month_index, instead of always index=0) did NOT
resolve the identical-results problem on a real bulk run — Stockport,
Bolton, and others still showed byte-identical "Total across N pages: X"
results across every one of 7 month attempts, even with the fix in place.

Rather than guess at a third fix and burn another ~3-hour, 210-council
bulk run to find out if it worked, this script directly reuses the REAL
IdoxPortal._scrape_month() method (imported from idox_scraper.py, not
reimplemented) against just 3 councils and 3 spread-out month indices
(0, 3, 6) — a few minutes, not hours — and prints a clear side-by-side
comparison of what each month actually returned.

If the reference numbers genuinely differ across months: month selection
is working, and the earlier bulk run's identical results were likely a
symptom of something else (rate-limiting, a stale browser page being
reused in a way that didn't reset properly, etc.) — worth investigating
further before another bulk run.

If the reference numbers are identical across months: month selection is
still broken, and the round-4 diagnostic (added to idox_scraper.py
alongside this script) should reveal WHY — most likely either the
dropdown selector genuinely not matching this council's real HTML, or a
different failure mode the round-4 diagnostic can't see, in which case
we'll need real HTML evidence (a saved page dump) rather than another
guess.

Run via GitHub Actions workflow_dispatch (see scrape.yml — reuses a
manual-trigger pattern like arcus_recon).
"""
import asyncio
import sys
from datetime import date

# Import the REAL scraper code directly — not a reimplementation — so this
# test is guaranteed to reflect exactly what the actual bulk scrape does,
# including the round-3 and round-4 fixes already in idox_scraper.py.
from idox_scraper import IdoxPortal, CONTEXT_OPTIONS, BROWSER_ARGS
from playwright.async_api import async_playwright

# Real URLs, taken directly from idox_councils.py — small, deliberately
# varied sample: one council whose bulk run showed the identical-results
# symptom directly (Stockport), one more from the same symptom list
# (Bolton), and one from a different portal entirely (Rochdale, actually
# hosted on Oldham's shared server) to rule out "this is specific to one
# server" as an explanation.
TEST_COUNCILS = [
    ("Stockport Metropolitan Borough Council",
     "https://planning.stockport.gov.uk/PlanningData-live", 167),
    ("Bolton Metropolitan Borough Council",
     "https://paplanning.bolton.gov.uk/online-applications", 169),
    ("Rochdale Borough Council",
     "https://planningpa.oldham.gov.uk/online-applications", 172),
]

# Spread across the ~7-month bulk window rather than testing all 7 — this
# is meant to be fast. 0 = current month, 3 = three months back, 6 = six
# months back (roughly the oldest month bulk mode would request at
# DAYS_BACK=180).
TEST_MONTH_INDICES = [0, 3, 6]


def _month_index_to_date(month_index: int) -> date:
    m = date.today().replace(day=1)
    for _ in range(month_index):
        if m.month == 1:
            m = m.replace(year=m.year - 1, month=12)
        else:
            m = m.replace(month=m.month - 1)
    return m


async def test_council(browser, name: str, base_url: str, council_id: int):
    print(f"\n{'=' * 70}")
    print(f"Testing: {name}")
    print(f"{'=' * 70}")

    portal = IdoxPortal(name, base_url, council_id)
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    results_by_month = {}
    for month_index in TEST_MONTH_INDICES:
        target_month = _month_index_to_date(month_index)
        print(f"\n  --- month_index={month_index} ({target_month.strftime('%B %Y')}) ---")
        try:
            apps = await portal._scrape_month(page, target_month)
        except Exception as e:
            print(f"  ✗ Error: {e}")
            apps = []

        refs = sorted(a["reference"] for a in apps if a.get("reference"))
        sample = refs[:5]
        results_by_month[month_index] = {
            "count": len(apps),
            "all_refs": set(refs),
            "sample": sample,
        }
        print(f"  Count: {len(apps)}  |  Sample references: {sample}")

    await context.close()

    # --- The actual verdict for this council ---
    print(f"\n  --- Verdict for {name} ---")
    all_ref_sets = [v["all_refs"] for v in results_by_month.values()]
    counts = [v["count"] for v in results_by_month.values()]

    if len(set(counts)) == 1 and len(all_ref_sets) > 1 and all(
        s == all_ref_sets[0] for s in all_ref_sets
    ):
        print(f"  ⚠⚠⚠ IDENTICAL results across ALL tested months (same {counts[0]} "
              f"references every time). Month selection is NOT working for this council.")
    elif len(set(counts)) == 1:
        print(f"  ⚠ Same COUNT ({counts[0]}) across months, but checking reference "
              f"overlap for a more precise verdict...")
        overlap = all_ref_sets[0] & all_ref_sets[-1] if len(all_ref_sets) > 1 else set()
        total = all_ref_sets[0] | all_ref_sets[-1] if len(all_ref_sets) > 1 else set()
        overlap_pct = (len(overlap) / len(total) * 100) if total else 0
        print(f"  Reference overlap between first and last tested month: "
              f"{len(overlap)}/{len(total)} ({overlap_pct:.0f}%)")
        if overlap_pct > 90:
            print(f"  ⚠⚠⚠ Very high overlap — month selection likely still NOT working.")
        else:
            print(f"  ✓ Meaningfully different references despite similar counts — "
                  f"month selection APPEARS to be working (counts coincidentally similar).")
    else:
        print(f"  ✓ Different counts across months ({counts}) — "
              f"month selection IS working correctly for this council.")


async def main():
    print(f"Idox month-selection diagnostic — testing {len(TEST_COUNCILS)} councils "
          f"x {len(TEST_MONTH_INDICES)} month indices ({TEST_MONTH_INDICES})\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        for name, base_url, council_id in TEST_COUNCILS:
            await test_council(browser, name, base_url, council_id)
        await browser.close()

    print(f"\n{'=' * 70}")
    print("Diagnostic complete. Look for any '⚠⚠⚠' verdicts above — those are the")
    print("councils where month selection is confirmed still broken. Look for any")
    print("'MONTH DROPDOWN DIAGNOSTIC' lines interspersed above too — those tell you")
    print("WHY, if the dropdown-selector-not-found theory is correct.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
