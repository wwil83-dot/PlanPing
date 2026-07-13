#!/usr/bin/env python3
"""
PlanFind — Arcus reconnaissance script.

Arcus planning portals (Manchester, Salford, Ashford, Epping Forest,
Bracknell Forest, Milton Keynes, and others — all confirmed running the
Arcus Built Environment product on Salesforce Experience Cloud/Lightning)
are heavily JavaScript-rendered single-page apps. Unlike Idox, where the
HTML structure was well-documented across years of forum posts and could
be inferred confidently before writing any scraper code, nobody appears to
have published what the RENDERED DOM looks like once Salesforce Lightning
finishes loading.

This script does NOT attempt to parse or extract structured data. Its only
job is to:
  1. Load a target Arcus register-view page with a generous wait for
     Lightning components to finish rendering (not just domcontentloaded).
  2. Attempt to find and click through to a "Weekly List" or equivalent
     date-filtered view, since that's the Arcus analogue of Idox's
     monthlyList/weeklyList mechanism.
  3. Save the fully-rendered HTML and a screenshot at each stage, plus some
     basic diagnostic counts (tables, common Lightning component tags),
     so a human (or Claude, next session) can look at real captured output
     and design the actual production scraper's selectors against ground
     truth rather than guesses.

Run via GitHub Actions workflow_dispatch (see scrape.yml: arcus_recon job).
Outputs are uploaded as a workflow artifact — download it from the Actions
run summary page after it completes.
"""
import asyncio
import sys
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Target council for this recon run. Ashford chosen because it's the one
# confirmed Arcus council where a search engine actually managed to index
# SOME real rendered content (one result showed genuine application
# details), suggesting it's not permanently stuck on the loading screen —
# a reasonable first council to test against.
# ---------------------------------------------------------------------------
TARGET_URL = "https://ashfordboroughcouncil.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register"
TARGET_NAME = "Ashford Borough Council"

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
}


def _diagnostic_summary(html: str) -> str:
    """Cheap, dependency-free counts of things worth knowing about the DOM
    structure — no real parsing, just enough to tell a human what shape
    the page actually took (table-based? lightning-datatable? custom divs?).
    """
    tags_of_interest = [
        "<table", "<tr", "<td", "lightning-datatable", "lightning-formatted",
        "slds-table", "role=\"row\"", "role=\"grid\"", "<lst-", "<c-",
        "Sorry to interrupt", "Loading",
    ]
    lines = [f"HTML length: {len(html)} chars"]
    for tag in tags_of_interest:
        lines.append(f"  Count of '{tag}': {html.count(tag)}")
    return "\n".join(lines)


async def _save_stage(page, stage_name: str):
    """Save HTML + screenshot for one point in the navigation, and print a
    diagnostic summary so useful signal shows up in the Action log itself
    even before anyone downloads the artifact.
    """
    html = await page.content()
    title = await page.title()

    html_path = f"/tmp/arcus_recon_{stage_name}.html"
    png_path = f"/tmp/arcus_recon_{stage_name}.png"

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        await page.screenshot(path=png_path, full_page=True)
    except Exception as e:
        print(f"    ⚠ Screenshot failed: {e}")

    print(f"\n--- Stage: {stage_name} ---")
    print(f"Page title: '{title}'")
    print(f"Saved: {html_path}, {png_path}")
    print(_diagnostic_summary(html))


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Arcus recon — {TARGET_NAME}")
    print(f"Target URL: {TARGET_URL}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        # --- Stage 1: initial load, minimal wait (like Idox's domcontentloaded) ---
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)
        except PlaywrightTimeout:
            print("⚠ Initial page load timed out entirely — site may be down or blocking.")
            await browser.close()
            return
        except Exception as e:
            print(f"⚠ Initial navigation error: {e}")
            await browser.close()
            return

        await _save_stage(page, "01_initial_load")

        # --- Stage 2: generous wait for Lightning to finish rendering ---
        # Salesforce Lightning apps often need several seconds AFTER
        # domcontentloaded to actually populate content via internal API
        # calls. Idox never needed this — its HTML was already complete on
        # arrival. Try networkidle first (waits for network quiet), then
        # add a flat extra wait as a backstop since Lightning apps sometimes
        # keep background polling that never truly goes idle.
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            print("⚠ networkidle wait timed out — page may have persistent background activity (common for Lightning apps)")
        await asyncio.sleep(5)

        await _save_stage(page, "02_after_wait")

        # --- Stage 3: try to find and click through to a weekly/date list ---
        # Best-effort — we don't know the real selector yet, so try several
        # plausible link texts seen in Arcus help documentation ("Weekly
        # List", "Planning Applications Weekly List", "User Defined Weekly
        # List"). If none match, that's itself useful diagnostic information
        # for next time — better than crashing.
        clicked = False
        for link_text in [
            "Planning Applications Weekly List",
            "User Defined Weekly List",
            "Weekly List",
            "Weekly Lists",
        ]:
            try:
                loc = page.get_by_text(link_text, exact=False)
                if await loc.count() > 0:
                    await loc.first.click(timeout=5_000)
                    clicked = True
                    print(f"✓ Clicked link matching: '{link_text}'")
                    break
            except Exception:
                continue

        if clicked:
            await asyncio.sleep(5)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            await _save_stage(page, "03_after_weekly_list_click")
        else:
            print("\n⚠ Could not find any weekly-list link with the text variants tried — "
                  "the actual link text/structure will need to be read from the "
                  "02_after_wait HTML/screenshot directly.")

        await browser.close()

    print("\n=== Recon complete ===")
    print("Download the workflow artifact to inspect the saved HTML/screenshots.")


if __name__ == "__main__":
    asyncio.run(main())
