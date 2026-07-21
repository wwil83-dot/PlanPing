#!/usr/bin/env python3
"""Test for the multi-category tabbed_weekly_list dispatch fix
(2026-07-21) — proves the new list-based config calls
_scrape_tabbed_weekly_list once per category and aggregates results,
while a single string or None (Eastleigh, Anglesey) still behaves
exactly as before this change."""
import asyncio
from unittest.mock import AsyncMock, patch

import arcus_scraper as mod


async def run():
    checks = []

    # 1. config as a LIST of 2 categories (Wiltshire) -> the underlying
    # method gets called twice, once per category, with results aggregated
    portal_multi = mod.ArcusPortal(
        "Wiltshire Council", "https://development.wiltshire.gov.uk/pr/s",
        "tabbed_weekly_list",
        ["Category A", "Category B"],
        437,
    )
    call_log = []

    async def fake_tabbed(self, page, category_hint):
        call_log.append(category_hint)
        return [{"reference": f"REF-{category_hint}"}]

    fake_browser = AsyncMock()
    fake_context = AsyncMock()
    fake_page = AsyncMock()
    fake_browser.new_context.return_value = fake_context
    fake_context.new_page.return_value = fake_page

    with patch.object(mod.ArcusPortal, "_scrape_tabbed_weekly_list", fake_tabbed):
        apps = await portal_multi.scrape(fake_browser)

    checks.append((
        "list config calls the method once per category",
        call_log == ["Category A", "Category B"]
    ))
    checks.append((
        "list config aggregates results from both calls",
        len(apps) == 2 and {a["reference"] for a in apps} == {"REF-Category A", "REF-Category B"}
    ))

    # 2. config as a single string (Anglesey-style) -> still calls the
    # method exactly once, unchanged from before this fix
    portal_single = mod.ArcusPortal(
        "Isle of Anglesey County Council", "https://ioacc.my.site.com/s/pr-english",
        "tabbed_weekly_list", "Some Category", 430,
    )
    call_log_single = []

    async def fake_tabbed_single(self, page, category_hint):
        call_log_single.append(category_hint)
        return [{"reference": "REF-single"}]

    with patch.object(mod.ArcusPortal, "_scrape_tabbed_weekly_list", fake_tabbed_single):
        apps_single = await portal_single.scrape(fake_browser)

    checks.append((
        "single-string config still calls the method exactly once",
        call_log_single == ["Some Category"]
    ))

    # 3. config as None (Eastleigh-style) -> still calls the method once
    # with None, unchanged
    portal_none = mod.ArcusPortal(
        "Eastleigh Borough Council", "https://planning.eastleigh.gov.uk/s/public-register",
        "tabbed_weekly_list", None, 301,
    )
    call_log_none = []

    async def fake_tabbed_none(self, page, category_hint):
        call_log_none.append(category_hint)
        return [{"reference": "REF-none"}]

    with patch.object(mod.ArcusPortal, "_scrape_tabbed_weekly_list", fake_tabbed_none):
        apps_none = await portal_none.scrape(fake_browser)

    checks.append((
        "None config still calls the method exactly once with None",
        call_log_none == [None]
    ))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
