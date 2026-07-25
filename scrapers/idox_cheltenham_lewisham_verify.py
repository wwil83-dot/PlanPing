#!/usr/bin/env python3
"""
PlanFind — Cheltenham/Lewisham redirect verification (2026-07-25).

CRITICAL CHECK before trusting idox_multi_recon.py's round-4 finding
that Cheltenham and Lewisham are genuinely working. Existing, trusted
production config has "Ipswich Borough Council" using Cheltenham's exact
URL, and "London Borough of Waltham Forest" using Lewisham's exact URL —
both with a comment explaining a real, previously-confirmed redirect.
Ward-dropdown names alone (genuinely Cheltenham/Lewisham-specific) are
suggestive but NOT conclusive — this submits the REAL monthly-list form
and checks actual application addresses/references, which is
definitive: if results show genuine Cheltenham (GL postcodes) or
Lewisham (SE postcodes) addresses, the old redirect is stale and it's
safe to add these as real, separate councils. If results show Ipswich
(IP postcodes) or Waltham Forest (E postcodes) addresses instead, the
old finding still holds and these URLs must NOT be added as separate
councils — the existing Ipswich/Waltham Forest entries are correct as-is.
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
    ("Cheltenham (claimed URL)", "https://publicaccess.cheltenham.gov.uk/online-applications",
     "genuine Cheltenham addresses should show GL postcodes; if this is really Ipswich's "
     "backend, expect IP postcodes instead"),
    ("Lewisham (claimed URL)", "https://planning.lewisham.gov.uk/online-applications",
     "genuine Lewisham addresses should show SE postcodes; if this is really Waltham "
     "Forest's backend, expect E postcodes instead"),
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
        print(f"  ⚠ Couldn't select month: {e}")
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
