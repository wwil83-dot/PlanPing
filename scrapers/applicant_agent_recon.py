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


async def _get_real_tabs(page) -> list:
    """Discover the REAL tabs this specific application page actually
    has — Idox installations vary in which tabs exist (Summary, Details,
    Contacts, Dates, Documents, Constraints, etc.), so this reads them
    directly from the page rather than guessing/hardcoding a fixed list
    that might not match Cotswold's real set at all."""
    tabs = []
    try:
        tab_links = page.locator("a[href*='activeTab=']")
        count = await tab_links.count()
        for i in range(count):
            href = await tab_links.nth(i).get_attribute("href")
            text = (await tab_links.nth(i).inner_text()).strip()
            if href and text:
                tabs.append((text, href))
    except Exception as e:
        print(f"  (tab discovery failed: {e})")
    return tabs


def _raw_context_snippets(html_lower: str, html_original: str, keyword: str, window: int = 150) -> list:
    """Real, honest evidence instead of a guessed extraction — shows
    the ACTUAL raw HTML surrounding every match, so it's directly
    visible whether this is a genuine structured field (e.g. a table
    row with a short label + short value) or boilerplate prose (a long
    sentence that just happens to contain the word). No cleverness that
    could itself be wrong in a new way — just the real surrounding
    markup for a human to judge."""
    snippets = []
    start = 0
    while True:
        idx = html_lower.find(keyword, start)
        if idx == -1:
            break
        snippet = html_original[max(0, idx - window):idx + len(keyword) + window]
        snippets.append(snippet.replace("\n", " ").strip())
        start = idx + len(keyword)
        if len(snippets) >= 3:  # cap per keyword, per tab — plenty to judge from
            break
    return snippets


async def check_detail_view(page, relative_link: str) -> dict:
    """Navigate to one real individual application's own detail page,
    then check EVERY real tab it actually has — not just the default
    Summary tab, since a genuine applicant/agent field may live on a
    completely different tab this recon would otherwise never see."""
    from urllib.parse import urljoin
    url = urljoin(page.url, relative_link)

    print(f"\nNavigating to individual application detail page: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        return {"error": "Page load timeout on application detail"}

    real_tabs = await _get_real_tabs(page)
    print(f"  Real tabs found on this page: {[t[0] for t in real_tabs] if real_tabs else '(none found — single-page layout?)'}")

    results_by_tab = {}
    # Always check the current (default/Summary) tab first, then every
    # other real tab discovered above
    tabs_to_check = [("(default/current tab)", None)] + real_tabs

    for tab_name, tab_href in tabs_to_check:
        if tab_href:
            try:
                tab_url = urljoin(page.url, tab_href)
                await page.goto(tab_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1000)
            except Exception as e:
                results_by_tab[tab_name] = {"error": f"Could not load tab: {e}"}
                continue

        html = await page.content()
        html_lower = html.lower()

        tab_findings = {}
        for kw in APPLICANT_AGENT_KEYWORDS:
            if kw in html_lower:
                tab_findings[kw] = _raw_context_snippets(html_lower, html, kw)

        if tab_findings:
            results_by_tab[tab_name] = tab_findings

    return {
        "url_loaded": url,
        "real_tabs_found": [t[0] for t in real_tabs],
        "findings_by_tab": results_by_tab,
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
            print("\n--- STEP 2: Checking EVERY real tab on an individual APPLICATION DETAIL view ---")
            detail_result = await check_detail_view(page, sample_link)

            if detail_result.get("error"):
                print(f"  {detail_result['error']}")
            else:
                print(f"  URL loaded: {detail_result['url_loaded']}")
                print(f"  Real tabs found: {detail_result['real_tabs_found']}")

                findings = detail_result.get("findings_by_tab", {})
                if not findings:
                    print("\n*** REAL FINDING: no applicant/agent-related text found on ANY tab ***")
                else:
                    print(f"\n*** REAL FINDING: matches found on {len(findings)} tab(s) — "
                          f"raw context below, judge for yourself whether this is a genuine "
                          f"structured field or just prose mentioning the word ***")
                    for tab_name, tab_findings in findings.items():
                        print(f"\n  [{tab_name}]")
                        for kw, snippets in tab_findings.items():
                            print(f"    keyword {kw!r}:")
                            for s in snippets:
                                print(f"      ...{s}...")
        else:
            print("\nNo individual application link found on the list page — could not check STEP 2.")

        await browser.close()

    print("\n" + "=" * 70)
    print("Recon complete.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
