#!/usr/bin/env python3
"""
PlanFind — multi-council Idox recon (round 4, 2026-07-25).

PURPOSE: idox_gap_prober.py's batch HTTP scan found 7 real hits against
121 gap-list candidates (councils with no confirmed vendor at all) —
Brentwood, Cheltenham, Chesterfield, Hastings, Lewisham, Redbridge,
Bridgend. That was a lightweight, unauthenticated HTTP check only
(page loaded + contained Idox-flavoured text) — this does the REAL
verification, same as every other round: actually navigate, check for
a working month/date search form, confirm results actually render.
Hits here are what's safe to add to idox_councils.py; anything that
fails needs individual follow-up, not automatic trust either way.

All 7 targets use standard monthly mode (no evidence yet of a "weekly"
council among them).
"""
import asyncio
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

# (name, base_url, mode)
TARGETS = [
    ("Brentwood Borough Council", "https://publicaccess.brentwood.gov.uk/online-applications", "monthly"),
    ("Cheltenham Borough Council", "https://publicaccess.cheltenham.gov.uk/online-applications", "monthly"),
    ("Hastings Borough Council", "https://hastings.gov.uk/online-applications", "monthly"),
    ("London Borough of Lewisham", "https://planning.lewisham.gov.uk/online-applications", "monthly"),
    ("London Borough of Redbridge", "https://planning.redbridge.gov.uk/online-applications", "monthly"),
    ("Bridgend County Borough Council", "https://planning.bridgend.gov.uk/online-applications", "monthly"),
]
# Chesterfield DELIBERATELY DROPPED (2026-07-25) — confirmed already
# active in idox_councils.py (COUNCIL_DB_IDS id=317), a real error in
# the gap-list build 2 sessions ago (a fuzzy name-match miss), not a
# genuine new candidate.

RESULTS_CONTAINER_SELECTOR = (
    "ul.searchresults, #searchresults, div.searchresults, #searchResultsContainer"
)


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace(",", "").replace("(", "").replace(")", "")


async def recon_one(pw, name: str, base_url: str, mode: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {name}  (mode={mode})")
    print(f"Base URL: {base_url}")
    print("=" * 70)

    browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        if mode == "weekly":
            # Same session-establishing step production code uses before
            # hitting the weekly list directly.
            try:
                await page.goto(
                    f"{base_url}/search.do?action=simple&searchType=Application",
                    wait_until="domcontentloaded", timeout=30_000,
                )
                await asyncio.sleep(1)
            except Exception as e:
                print(f"  (session-establishing visit failed, continuing anyway: {e})")

            target_url = f"{base_url}/weeklyListResults.do?action=firstPage"
        else:
            target_url = (
                f"{base_url}/search.do?action=monthlyList"
                f"&searchCriteria.monthYearIndex=0&searchType=Application"
            )

        print(f"Navigating to: {target_url}")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(2)

    except PlaywrightTimeout:
        print("  ⚠ PAGE LOAD TIMEOUT — the exact failure mode seen in production.")
        print("  This means the request itself never completed within 45s —")
        print("  no title, no body to inspect. Points toward a network-level")
        print("  issue (connection stalling/dropping) rather than a page-content")
        print("  mismatch, since a WAF challenge page would normally still load.")
        await browser.close()
        return
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await browser.close()
        return

    title = await page.title()
    print(f"\nReal page title: {title!r}")

    # --- Results container check, using the exact same selector list
    # production code uses, so this is a direct yes/no on the real bug ---
    container = page.locator(RESULTS_CONTAINER_SELECTOR)
    container_count = await container.count()
    print(f"Results container match: {'YES (' + str(container_count) + ' found)' if container_count else 'NO — none of the known selectors matched'}")

    # --- Every <select> on the page ---
    selects = page.locator("select")
    select_count = await selects.count()
    print(f"\n<select> elements found: {select_count}")
    for i in range(select_count):
        sel = selects.nth(i)
        try:
            sel_id = await sel.get_attribute("id") or "(no id)"
            sel_name = await sel.get_attribute("name") or "(no name)"
            print(f"  Select #{i}: id={sel_id!r}, name={sel_name!r}")
            options = sel.locator("option")
            opt_count = await options.count()
            for j in range(min(opt_count, 8)):
                opt = options.nth(j)
                opt_value = await opt.get_attribute("value") or ""
                opt_text = await opt.inner_text()
                print(f"      [{j}] value={opt_value!r} text={opt_text!r}")
        except Exception as e:
            print(f"  Select #{i}: (error reading attributes: {e})")

    # --- Visible body text snippet — catches WAF/error pages directly ---
    try:
        body_text = await page.locator("body").inner_text()
        snippet = " ".join(body_text.split())[:500]
        print(f"\nVisible body text (first 500 chars):\n  {snippet!r}")
    except Exception as e:
        print(f"\n(couldn't extract body text: {e})")

    # --- Save full HTML as backup artifact ---
    html = await page.content()
    out_path = f"/tmp/idox_recon_{slug(name)}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nFull HTML saved: {out_path} ({len(html)} chars)")

    await browser.close()


async def main():
    print("PlanFind multi-council Idox recon")
    print(f"Targets: {', '.join(t[0] for t in TARGETS)}\n")

    async with async_playwright() as pw:
        for name, base_url, mode in TARGETS:
            await recon_one(pw, name, base_url, mode)

    print(f"\n{'=' * 70}")
    print("Recon complete for all targets.")


if __name__ == "__main__":
    asyncio.run(main())
