#!/usr/bin/env python3
"""
PlanFind — East Riding / Stafford / North Kesteven diagnostic (2026-08-25).

Real, confirmed: all 3 have genuinely never succeeded, even once —
distinct from the getApplications reputation-flag group. Real,
existing evidence from an earlier batch run suggests DIFFERENT causes,
not one shared problem:
  - East Riding: real "Too many results found. Please enter some more
    parameters" — the site's own search validation correctly rejecting
    an overly broad query, not a block/timeout. Also uses a different
    real URL path (/newplanningaccess, not the standard
    /online-applications).
  - Stafford: real "Nothing loaded — title: 'Monthly List'" — page
    loaded fine, but the results container never appeared. Could be
    genuinely slow, a real block, or a legitimate empty month.
  - North Kesteven: no specific real error captured yet — genuinely
    unknown.

Testing all 3 directly, isolated (no concurrency), with real network
activity logging, matching the same evidence-based approach already
proven throughout this project.
"""
import asyncio
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,
}

TARGETS = [
    ("East Riding of Yorkshire Council",
     "https://newplanningaccess.eastriding.gov.uk/newplanningaccess/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
    ("Stafford Borough Council",
     "https://www12.staffordbc.gov.uk/online-applications/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
    ("North Kesteven District Council",
     "https://planningonline.n-kesteven.gov.uk/online-applications/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def check_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"DIAGNOSTIC: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    network_log = []
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    page.on("response", lambda r: network_log.append((r.request.method, r.url, r.status)))

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error (real timeout/block signature): {e}")
        print(f"  Real network activity captured: {len(network_log)} entries")
        for method, resp_url, status in network_log[:10]:
            print(f"    {method} {status} {resp_url[:100]}")
        await context.close()
        return

    print(f"  Real HTTP status: {response.status if response else None}")
    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/never_succeeded_diag_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/never_succeeded_diag_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1500]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1500 chars): {body_text!r}")

    # Real, direct check for the confirmed result-container selectors
    # used elsewhere in idox_scraper.py
    for selector in ["ul.searchresults", "#searchresults", "div.searchresults", "#searchResultsContainer"]:
        try:
            count = await page.locator(selector).count()
            if count > 0:
                print(f"\n  Real result container found: {selector} ({count} matches)")
        except Exception:
            pass

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] East Riding / Stafford / "
          f"North Kesteven diagnostic — {len(TARGETS)} councils, isolated\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, url in TARGETS:
            await check_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
