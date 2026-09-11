#!/usr/bin/env python3
"""
PlanFind — Fylde-pattern cluster recon (2026-09-11, round 2).

Real question: 7 councils were flagged as potentially sharing a
platform pattern similar to Fylde's — Worcester, Vale of Glamorgan,
Bridgend, Vale of White Horse, South Oxfordshire, West Northants,
Welwyn Hatfield. Manual browsing notes split them into two groups:

  1. Four confirmed on the EXACT SAME third-party vendor domain
     (planning-register.co.uk) — Vale of White Horse, South
     Oxfordshire, West Northants, and (very likely) Vale of Glamorgan
     via its "vogonline" subdomain.
  2. Three on their own domains (Worcester, Bridgend, Welwyn Hatfield)
     that LOOK visually similar in the manual notes but are NOT yet
     confirmed to be the same underlying platform — "looks similar" is
     not confirmation, the exact lesson already learned the hard way
     with Fylde and Kirklees being wrongly assumed Idox earlier in
     this project.

ROUND 2 UPGRADE — now that fylde_scraper.py's real, confirmed code is
available, this tests for Fylde's OWN specific platform fingerprints
directly, rather than just dumping generic form fields for manual
comparison:
  - The exact field ids #DateReceivedFrom / #DateReceivedTo
  - A disclaimer gate redirect before reaching the real search page
  - Results tables with class "tblResults" (checked only if a real
    test submission succeeds)
  - The distinctive pagination link aria-label="Next Page." (note the
    literal trailing period — genuinely distinctive, not a generic
    "Next" label most platforms use)

Any candidate matching several of these exactly is very likely running
the SAME underlying platform as Fylde, not just a visually similar one
— the strongest evidence this recon can offer without a human directly
comparing screenshots.
"""
import asyncio
from datetime import date, timedelta

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

# Real, manually-checked URLs — see module docstring. Kept exactly as
# provided, not reconstructed from a web search (the same mistake that
# produced wrong URLs for Fylde earlier in this project).
CANDIDATES = [
    ("Worcester City Council", "https://plan.worcester.gov.uk/Search/Advanced"),
    ("Vale of Glamorgan Council", "https://vogonline.planning-register.co.uk/Search/Advanced"),
    ("Bridgend County Borough Council", "https://planning.bridgend.gov.uk/Search/Planning/Advanced"),
    ("Vale of White Horse District Council", "https://valeofwhitehorse.planning-register.co.uk/Search/Advanced"),
    ("South Oxfordshire District Council", "https://southoxfordshire.planning-register.co.uk/Search/Advanced"),
    ("West Northamptonshire Council", "https://wnc.planning-register.co.uk/Search/Advanced"),
    ("Welwyn Hatfield Borough Council", "https://planning.welhat.gov.uk/Search/Advanced"),
]


def _safe_name(name: str) -> str:
    return name.lower().replace(" ", "_").replace(",", "")


async def click_through_disclaimer(page, label: str) -> bool:
    """REAL BUG FIX (round 3) — round 2's fingerprint/field checks ran
    BEFORE this click for every disclaimer-gated site, meaning they
    were only ever describing the disclaimer page itself (1-2 generic
    fields, 0/4 fingerprints) rather than the real search form behind
    it. Moved to run FIRST, unconditionally, before any real checks.
    Also: round 2's click selector (button:has-text('Agree')) timed
    out for 4 of 5 disclaimer-gated sites — rather than guess at a
    different button text blind, this captures the REAL disclaimer
    markup directly when no match is found, the same evidence-first
    approach already used for Telford's error pages."""
    if "Disclaimer" not in page.url:
        return True  # no disclaimer gate on this site at all

    # Try several real, plausible button/link texts before giving up —
    # but capture the REAL markup regardless, so a genuine miss is
    # diagnosable rather than silently guessed at again next round.
    for selector in [
        "button:has-text('Agree')", "input[value='Agree']",
        "button:has-text('Accept')", "input[value='Accept']",
        "button:has-text('Continue')", "input[value='Continue']",
        "button:has-text('I Agree')", "a:has-text('Agree')",
        "a:has-text('Accept')", "a:has-text('Continue')",
    ]:
        try:
            loc = page.locator(selector)
            if await loc.count() > 0:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                    await loc.first.click(timeout=5_000)
                print(f"    [{label}] Real disclaimer click succeeded via selector: {selector!r}")
                return True
        except Exception:
            continue

    # None of the guessed selectors worked — capture the real markup
    # directly rather than guessing a third time.
    print(f"    [{label}] ⚠ Could not click through disclaimer with any known "
          f"selector — capturing real button/link markup:")
    buttons = await page.locator("button, input[type='submit'], input[type='button'], a.button, a.btn").all()
    for b in buttons[:15]:
        try:
            text = (await b.inner_text()).strip()
            outer = await b.evaluate("el => el.outerHTML")
            print(f"      {outer[:200]!r}  (visible text: {text!r})")
        except Exception:
            continue
    return False


async def check_fylde_fingerprints(page, label: str) -> dict:
    """Tests for Fylde's own specific, confirmed platform markers
    directly, rather than generic field names — a real match here is
    much stronger evidence of a shared platform than visual similarity
    alone."""
    fingerprints = {}

    date_from_count = await page.locator("#DateReceivedFrom").count()
    date_to_count = await page.locator("#DateReceivedTo").count()
    fingerprints["has_DateReceivedFrom"] = date_from_count > 0
    fingerprints["has_DateReceivedTo"] = date_to_count > 0

    tbl_results_count = await page.locator("table.tblResults").count()
    fingerprints["tblResults_table_count"] = tbl_results_count

    next_page_link_count = await page.locator("a[aria-label='Next Page.']").count()
    fingerprints["has_exact_NextPage_ariaLabel"] = next_page_link_count > 0

    print(f"    [{label}] FYLDE FINGERPRINT CHECK:")
    for k, v in fingerprints.items():
        print(f"      {k}: {v}")

    match_count = sum([
        fingerprints["has_DateReceivedFrom"],
        fingerprints["has_DateReceivedTo"],
        fingerprints["tblResults_table_count"] > 0,
        fingerprints["has_exact_NextPage_ariaLabel"],
    ])
    print(f"    [{label}] Real fingerprint matches: {match_count} of 4")
    return fingerprints


async def inspect_form_fields(page, label: str):
    """Real evidence, not assumed field names — finds every real
    input/select on the page and logs its name/id/type, so genuinely
    identical platforms show identical field signatures and different
    platforms show up as different, rather than guessing from visual
    similarity in a screenshot alone."""
    inputs = await page.locator("input").all()
    print(f"    [{label}] Found {len(inputs)} real <input> elements:")
    for inp in inputs[:30]:  # cap for readability — most of these
                              # forms have well under 30 real fields
        try:
            name = await inp.get_attribute("name")
            id_ = await inp.get_attribute("id")
            type_ = await inp.get_attribute("type")
            if name or id_:
                print(f"      input: name={name!r} id={id_!r} type={type_!r}")
        except Exception:
            continue

    selects = await page.locator("select").all()
    print(f"    [{label}] Found {len(selects)} real <select> elements:")
    for sel in selects[:15]:
        try:
            name = await sel.get_attribute("name")
            id_ = await sel.get_attribute("id")
            if name or id_:
                print(f"      select: name={name!r} id={id_!r}")
        except Exception:
            continue


async def try_fylde_style_search(page, label: str) -> bool:
    """Best-effort attempt at Fylde's own exact real search flow —
    filling the exact real #DateReceivedFrom/#DateReceivedTo fields
    and submitting (disclaimer, if any, is already handled by the
    caller before this runs). Returns True if this actually succeeded
    (meaning the candidate's real field ids genuinely match Fylde's),
    False otherwise. A failure here is expected and fine for
    candidates on a different platform — it's real evidence either
    way, not an error."""
    today = date.today()
    start = today - timedelta(days=30)
    start_str = start.strftime("%d/%m/%Y")
    end_str = today.strftime("%d/%m/%Y")

    try:
        date_from = page.locator("#DateReceivedFrom")
        date_to = page.locator("#DateReceivedTo")
        if await date_from.count() == 0 or await date_to.count() == 0:
            print(f"    [{label}] Real fields #DateReceivedFrom/#DateReceivedTo "
                  f"not present — not attempting Fylde-style submission")
            return False

        await date_from.first.fill(start_str, timeout=5_000)
        await date_to.first.fill(end_str, timeout=5_000)

        # REAL FIX (round 3) — Worcester and Welwyn Hatfield's real
        # forms have MANY buttons (checkboxes, other search sections),
        # so the generic ".last" match from round 2 likely grabbed the
        # wrong element. Trying several plausible real submit
        # candidates explicitly, checking each is actually visible
        # before clicking, rather than blindly trusting position.
        submit_selectors = [
            "button:has-text('Search'):visible",
            "input[type='submit']:visible",
            "button[type='submit']:visible",
            "input[value='Search']:visible",
        ]
        clicked = False
        for sel in submit_selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
                if count > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
                        await loc.last.click(timeout=5_000)
                    clicked = True
                    print(f"    [{label}] Submitted via selector: {sel!r} "
                          f"({count} real match(es) found)")
                    break
            except Exception:
                continue

        if not clicked:
            print(f"    [{label}] No real submit button could be clicked "
                  f"successfully — fields exist but submission failed")
            return False

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"    [{label}] Fylde-style search SUCCEEDED — real post-submit "
              f"URL: {page.url}")
        return True
    except Exception as e:
        print(f"    [{label}] Fylde-style search did not complete "
              f"({type(e).__name__}) — not necessarily an error, may just "
              f"be a different platform")
        return False


async def recon_one(browser, council_name: str, url: str):
    print(f"\n{'=' * 60}")
    print(f"RECON: {council_name}")
    print(f"URL: {url}")
    print(f"{'=' * 60}")

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"    ⚠ Page load failed: {e}")
        await context.close()
        return

    status = response.status if response else None
    title = await page.title()
    print(f"    Real HTTP status: {status}")
    print(f"    Real page title: {title!r}")
    print(f"    Real final URL (before any disclaimer click): {page.url}")

    # REAL FIX (round 3) — click through any disclaimer FIRST, before
    # any fingerprint/field checks, so those checks describe the real
    # search form rather than the disclaimer page itself.
    reached_real_form = await click_through_disclaimer(page, council_name)
    if reached_real_form:
        print(f"    Real URL after disclaimer handling: {page.url}")

    await check_fylde_fingerprints(page, council_name)
    await inspect_form_fields(page, council_name)

    safe = _safe_name(council_name)
    await page.screenshot(path=f"/tmp/fylde_cluster_{safe}_search.png")

    search_succeeded = False
    if reached_real_form:
        search_succeeded = await try_fylde_style_search(page, council_name)
    if search_succeeded:
        # Real confirmation — re-check the fingerprints on the RESULTS
        # page specifically, since tblResults/Next-Page only exist
        # after a real submission, not on the bare search form.
        await check_fylde_fingerprints(page, f"{council_name} (results page)")
        await page.screenshot(path=f"/tmp/fylde_cluster_{safe}_results.png")
        html = await page.content()
        with open(f"/tmp/fylde_cluster_{safe}_results.html", "w", encoding="utf-8") as f:
            f.write(html)

    html = await page.content()
    with open(f"/tmp/fylde_cluster_{safe}_search.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"    Saved screenshots and HTML for {council_name}")

    await context.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for council_name, url in CANDIDATES:
            try:
                await recon_one(browser, council_name, url)
            except Exception as e:
                print(f"\n⚠ Unexpected error reconning {council_name}: {e}")

        await browser.close()

    print(f"\n{'=' * 60}")
    print("Recon complete. Any candidate scoring 4/4 real Fylde fingerprint "
          "matches (or a successful Fylde-style search) is very likely "
          "running the SAME underlying platform as Fylde itself — a much "
          "stronger claim than 'looks visually similar'.")


if __name__ == "__main__":
    asyncio.run(main())
