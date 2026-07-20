#!/usr/bin/env python3
"""
PlanFind — multi-council Idox recon (2026-07-20).

PURPOSE: council_health_check.py flagged 4 councils failing PERSISTENTLY
(not just on a noisy timeout night) with a distinct, stable signature each
— Renfrewshire, Brighton and Hove, Tonbridge and Malling, London Borough
of Brent. Rather than guess a fix for any of them, this captures direct
HTML evidence from each one's real page, the same "get real evidence
before fixing" principle that cracked the month-dropdown mystery earlier.

IMPORTANT: Renfrewshire is configured as a "weekly" council in
idox_councils.py (use_weekly_list=True) — it goes through a completely
different code path (_scrape_week, hitting weeklyListResults.do) than the
other three, which use the standard monthly-list flow. Probing it with a
monthly-list URL would test the wrong code entirely, so this script uses
the correct URL/flow for each council individually rather than one
shared template.

For each target, prints:
  - Real page title (confirms/refutes what production logs already showed)
  - Every <select> element's id/name/options (in case it's a dropdown
    mismatch, like Cheshire East's confirmed MONTH DROPDOWN DIAGNOSTIC)
  - Whether any of the known results-container selectors match
  - First 500 chars of visible body text (catches WAF/error pages)
Also saves full HTML per council to /tmp/ as a backup artifact.

Run via GitHub Actions workflow_dispatch (see scrape.yml) — add a new
manual-trigger job the same way idox_month_test/idox_form_recon are set
up, or run locally with Playwright installed.
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

# (name, base_url, mode) — mode matches how idox_councils.py configures
# each one in production, so we're testing the code path that's actually
# failing, not a generic default.
TARGETS = [
    ("Renfrewshire Council", "https://pl-bs.renfrewshire.gov.uk/online-applications", "weekly"),
    ("Brighton and Hove City Council", "https://planningapps.brighton-hove.gov.uk/online-applications", "monthly"),
    ("Tonbridge and Malling Borough Council", "https://publicaccess.tmbc.gov.uk/online-applications", "monthly"),
    ("London Borough of Brent", "https://pa.brent.gov.uk/online-applications", "monthly"),
]

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
