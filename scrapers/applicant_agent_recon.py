#!/usr/bin/env python3
"""
PlanFind — applicant/agent data availability recon (2026-08-13).

PURPOSE: Before committing any code to exposing applicant/agent
intelligence (flagged as the single biggest commercial opportunity in
an external product assessment, but with a real, unverified concern
about the actual architecture cost), this checks directly: does a real
Idox monthly-list RESULTS page expose applicant/agent name at all, or
does that only ever appear on each INDIVIDUAL application's own detail
page?

This matters enormously for scope. If it's on the list view, exposing
it is a genuinely small, low-risk change — the data's already sitting
in every page we scrape nightly, just not extracted. If it only exists
on individual application detail pages, exposing it at any real scale
means visiting a SEPARATE page per application rather than one page per
~10 applications (the list view) — a 10x+ increase in request volume
per council, directly compounding the exact WAF/429 blocking problem
this whole session fought hard to understand and partially mitigate.

Real, direct evidence only — no assumptions. Targets a council already
confirmed reliably working all session, so a failure here means
something about applicant/agent data specifically, not general
scraper flakiness.
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

# Cotswold District Council — chosen because it's shown up as reliably
# working across multiple nights this session (real, confirmed
# successful saves, not just configured).
TARGET_NAME = "Cotswold District Council"
TARGET_BASE_URL = "https://publicaccess.cotswold.gov.uk/online-applications"

APPLICANT_AGENT_KEYWORDS = [
    "applicant", "agent name", "agent:", "applicant name",
    "applicant's name", "agent's name",
]


async def check_list_view(page) -> dict:
    """Navigate to a real monthly-list results page and inspect what
    fields are genuinely present — not guessing from memory."""
    url = (
        f"{TARGET_BASE_URL}/search.do"
        f"?action=monthlyList&searchCriteria.monthYearIndex=1&searchType=Application"
    )
    print(f"Navigating to monthly list: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except PlaywrightTimeout:
        return {"error": "Page load timeout on monthly list"}

    # Real production flow: select month, tick date-received radio, submit
    try:
        month_select = page.locator("select[name*='month'], #month")
        if await month_select.count() > 0:
            await month_select.first.select_option(index=1)
    except Exception as e:
        print(f"  (month select skipped: {e})")

    try:
        radio = page.locator("input[type=radio]").first
        if await radio.count() > 0:
            await radio.check()
    except Exception as e:
        print(f"  (radio select skipped: {e})")

    try:
        submit = page.locator("input[type=submit], button[type=submit]").first
        await submit.click()
        await page.wait_for_selector(
            "ul.searchresults, #searchresults, div.searchresults, #searchResultsContainer",
            timeout=25_000,
        )
    except Exception as e:
        return {"error": f"Could not submit/load results: {e}"}

    body_text = (await page.inner_text("body")).lower()
    found_keywords = [kw for kw in APPLICANT_AGENT_KEYWORDS if kw in body_text]

    # Also check the raw HTML structure directly — a keyword search on
    # visible text alone could miss data sitting in an attribute or a
    # collapsed/hidden element
    html = await page.content()
    html_lower = html.lower()
    found_in_html = [kw for kw in APPLICANT_AGENT_KEYWORDS if kw in html_lower]

    # Grab a real link to an individual application, for the next check
    first_app_link = None
    try:
        link = page.locator("a[href*='applicationDetails.do']").first
        if await link.count() > 0:
            first_app_link = await link.get_attribute("href")
    except Exception:
        pass

    return {
        "list_view_text_matches": found_keywords,
        "list_view_html_matches": found_in_html,
        "sample_application_link": first_app_link,
    }


async def check_detail_view(page, relative_link: str) -> dict:
    """Navigate to one real individual application's own detail page —
    the actual test of whether applicant/agent data lives there instead."""
    if relative_link.startswith("http"):
        url = relative_link
    else:
        url = f"{TARGET_BASE_URL}/{relative_link.lstrip('/')}"

    print(f"\nNavigating to individual application detail page: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        return {"error": "Page load timeout on application detail"}

    body_text = (await page.inner_text("body")).lower()
    found_keywords = [kw for kw in APPLICANT_AGENT_KEYWORDS if kw in body_text]

    html = await page.content()
    found_in_html = [kw for kw in APPLICANT_AGENT_KEYWORDS if kw in html.lower()]

    # Real, direct extraction attempt — not just keyword presence, but
    # actually pulling out real text near an "Applicant"/"Agent" label
    # if one exists, so we have real sample data, not just a yes/no
    real_samples = {}
    for label in ["Applicant Name", "Applicant", "Agent Name", "Agent"]:
        try:
            locator = page.get_by_text(label, exact=False).first
            if await locator.count() > 0:
                # Try to grab the sibling/following text — real page
                # structures vary, so this is best-effort, not guaranteed
                parent_text = await locator.locator("xpath=..").inner_text()
                real_samples[label] = parent_text.strip()[:200]
        except Exception:
            pass

    return {
        "detail_view_text_matches": found_keywords,
        "detail_view_html_matches": found_in_html,
        "real_sample_extracts": real_samples,
        "full_page_title": await page.title(),
    }


async def main():
    print("=" * 70)
    print(f"APPLICANT/AGENT DATA RECON — {TARGET_NAME}")
    print("=" * 70)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        print("\n--- STEP 1: Checking the LIST/RESULTS view ---")
        list_result = await check_list_view(page)
        for k, v in list_result.items():
            print(f"  {k}: {v}")

        if list_result.get("error"):
            print("\nCould not check list view — stopping here.")
            await browser.close()
            return

        if list_result.get("list_view_text_matches") or list_result.get("list_view_html_matches"):
            print("\n*** REAL FINDING: applicant/agent-related text found on the LIST view ***")
        else:
            print("\n*** REAL FINDING: no applicant/agent-related text found on the LIST view ***")

        sample_link = list_result.get("sample_application_link")
        if sample_link:
            print("\n--- STEP 2: Checking an individual APPLICATION DETAIL view ---")
            detail_result = await check_detail_view(page, sample_link)
            for k, v in detail_result.items():
                print(f"  {k}: {v}")

            if detail_result.get("detail_view_text_matches") or detail_result.get("detail_view_html_matches"):
                print("\n*** REAL FINDING: applicant/agent-related text found on the DETAIL view ***")
            else:
                print("\n*** REAL FINDING: no applicant/agent-related text found on the DETAIL view either ***")
        else:
            print("\nNo individual application link found on the list page — could not check STEP 2.")

        await browser.close()

    print("\n" + "=" * 70)
    print("Recon complete.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
