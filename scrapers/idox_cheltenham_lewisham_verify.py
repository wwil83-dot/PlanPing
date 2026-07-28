#!/usr/bin/env python3
"""
PlanFind — follow-up domain verification (round 3, 2026-07-28).

CRITICAL CHECK: user-provided screenshots show real, distinct branding
(crests, council-specific nav, "Powered by idox"/"an idox solution"
footers) for THREE councils whose exact URLs are currently used in
production for DIFFERENT councils, based on real, previously-confirmed
redirect findings:
  - Tower Hamlets (development.towerhamlets.gov.uk) -> currently "Newham"
  - Plymouth (planning.plymouth.gov.uk) -> currently "Gloucester"
  - Greenwich (planning.royalgreenwich.gov.uk) -> currently "Richmond
    upon Thames"
Same exact pattern already found stale TWICE before this session
(Cheltenham/Ipswich, Lewisham/Waltham Forest) — branding alone is
suggestive but not as conclusive as real application addresses.
Submits the real monthly-list form for all 6 URLs and checks actual
addresses/postcodes, which settles each pair definitively.
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

TARGETS = [
    ("Tower Hamlets (claimed URL)", "https://development.towerhamlets.gov.uk/online-applications",
     "genuine Tower Hamlets addresses should show E1/E2/E3/E14 postcodes; if this is really "
     "Newham's backend, expect E6/E7/E12/E13/E15/E16 instead"),
    ("Newham (own real domain, per seed data)", "https://www.newham.gov.uk/online-applications",
     "if this now works on its own, real addresses should show E6/E7/E12/E13/E15/E16 postcodes"),
    ("Plymouth (claimed URL)", "https://planning.plymouth.gov.uk/online-applications",
     "genuine Plymouth addresses should show PL postcodes; if this is really Gloucester's "
     "backend, expect GL postcodes instead"),
    ("Gloucester (own real domain, per seed data)", "https://www.gloucester.gov.uk/online-applications",
     "if this now works on its own, real addresses should show GL postcodes"),
    ("Greenwich (claimed URL)", "https://planning.royalgreenwich.gov.uk/online-applications",
     "genuine Greenwich addresses should show SE postcodes; if this is really Richmond's "
     "backend, expect TW postcodes instead"),
    ("Richmond upon Thames (own real domain, per seed data)", "https://www.richmond.gov.uk/online-applications",
     "if this now works on its own, real addresses should show TW postcodes"),
]


async def full_monthly_flow(page, base_url: str, label: str):
    url = f"{base_url}/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    # Select the current month, click a date-received radio, submit —
    # same real production flow as idox_scraper.py
    try:
        await page.select_option("#month", index=0, timeout=5_000)
    except Exception as e:
        # DIAGNOSTIC (2026-07-28): a bare timeout here gave zero context
        # on a real run (Ipswich/Waltham Forest both failed with no way
        # to tell if it was a redirect, a wrong page, or something else)
        # — printing real evidence now instead of an unexplained timeout.
        title = await page.title()
        body_snippet = ""
        try:
            body_text = await page.locator("body").inner_text()
            body_snippet = " ".join(body_text.split())[:400]
        except Exception:
            pass
        print(f"  ⚠ Couldn't select month: {e}")
        print(f"  ⚠ DIAGNOSTIC — real title: {title!r}, body: {body_snippet!r}")
        return

    for radio_id in ["#searchCriteria\\.dateReceived", "input[id*='Received'][type='radio']"]:
        try:
            radio = page.locator(radio_id)
            if await radio.count() > 0:
                await radio.first.check(timeout=3_000)
                break
        except Exception:
            continue

    try:
        await page.click("#monthlyListForm input[type='submit']", timeout=5_000)
    except Exception as e:
        print(f"  ⚠ Couldn't submit: {e}")
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=25_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(2)

    title = await page.title()
    print(f"  Results page title: {title!r}")

    try:
        body_text = await page.locator("body").inner_text()
        # Print a generous chunk — enough to see several real addresses/
        # postcodes directly, which is the actual evidence needed here
        snippet = " ".join(body_text.split())[:1500]
        print(f"  Visible results text (first 1500 chars):\n    {snippet!r}")
    except Exception as e:
        print(f"  (couldn't extract body text: {e})")


async def main():
    print("Cheltenham/Lewisham redirect verification\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)

        for name, base_url, note in TARGETS:
            print(f"\n{'=' * 70}")
            print(f"VERIFYING: {name}")
            print(f"URL: {base_url}")
            print(f"What to look for: {note}")
            print("=" * 70)
            context = await browser.new_context(**CONTEXT_OPTIONS)
            page = await context.new_page()
            await full_monthly_flow(page, base_url, name)
            await context.close()

        await browser.close()

    print(f"\n{'=' * 70}")
    print("Verification complete. Check the printed addresses/postcodes")
    print("directly against the expected pattern for each council before")
    print("deciding whether to add these as real, separate councils.")


if __name__ == "__main__":
    asyncio.run(main())
