#!/usr/bin/env python3
"""
PlanFind — follow-up domain verification (round 2, 2026-07-28).

Cheltenham and Lewisham are now RESOLVED and in production (confirmed
2026-07-25 — real GL/SE postcode addresses, the old redirect findings
were stale). This round checks the 3 real questions left open from that
investigation:
  - Ipswich and Waltham Forest's OWN real domains (per existing seed
    data) — do these work now on their own, meaning the substituted
    Cheltenham/Lewisham workaround URLs are no longer necessary?
  - Redbridge — the gap-prober's guessed URL (planning.redbridge.gov.uk)
    returned a blank 39-byte response; existing seed data has a
    different, real subdomain (www.redbridge.gov.uk) worth checking
    properly before writing Redbridge off as broken.
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
    ("Ipswich (own real domain, per seed data)", "https://www.ipswich.gov.uk/online-applications",
     "if this now works on its own, real addresses should show IP postcodes — Ipswich's "
     "URL was substituted with Cheltenham's as a workaround at some point, worth checking "
     "if that's still necessary"),
    ("Waltham Forest (own real domain, per seed data)", "https://www.walthamforest.gov.uk/online-applications",
     "if this now works on its own, real addresses should show E postcodes — same "
     "situation as Ipswich, substituted with Lewisham's URL as a workaround"),
    ("Redbridge (corrected URL, per seed data)", "https://www.redbridge.gov.uk/online-applications",
     "the gap-prober's guess (planning.redbridge.gov.uk) returned a blank 39-byte response — "
     "this is the real URL from existing seed data, a different subdomain entirely"),
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
