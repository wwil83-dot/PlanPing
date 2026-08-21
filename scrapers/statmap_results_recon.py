#!/usr/bin/env python3
"""
PlanFind — statmap.co.uk/horizoNext, round 3: real Weekly Lists SEARCH
RESULTS recon (2026-08-21).

Round 2 confirmed the real Weekly Lists form (real field ids:
weekly-list-date-created-from / -to, plain text inputs with placeholder
DD/MM/YYYY, NOT readonly like Northgate's date-picker fields were) —
but never actually submitted a search, so no real results structure was
ever captured. This round fills in a real date range, clicks Search,
and captures whatever comes back — the real thing needed before
writing any parser.
"""
import asyncio
from datetime import date, timedelta, datetime, timezone

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
    ("West Lindsey District Council",
     "https://westlindsey-publicportal.statmap.co.uk/horizoNext/publicportal"),
    ("East Staffordshire Borough Council",
     "https://eaststaffs-publicportal.statmap.co.uk/horizoNext/publicportal"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def recon_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"WEEKLY LISTS SEARCH RESULTS RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    for label in ["Accept additional cookies", "Accept"]:
        try:
            btn = page.get_by_role("button", name=label, exact=False)
            if await btn.count() > 0 and await btn.first.is_visible(timeout=2000):
                await btn.first.click()
                await asyncio.sleep(1)
                break
        except Exception:
            continue

    try:
        weekly_btn = page.locator("button", has_text="Weekly Lists")
        await weekly_btn.first.click(timeout=10_000)
        print(f"  Clicked 'Weekly Lists' tab")
        await asyncio.sleep(1.5)
    except Exception as e:
        print(f"  ⚠ Could not click 'Weekly Lists': {e}")
        await context.close()
        return

    # Real, confirmed field ids from round 2, real DD/MM/YYYY format
    # matching the confirmed placeholder text
    today = date.today()
    start = today - timedelta(days=14)
    date_from_str = start.strftime("%d/%m/%Y")
    date_to_str = today.strftime("%d/%m/%Y")

    try:
        from_field = page.locator("#weekly-list-date-created-from")
        await from_field.fill(date_from_str, timeout=8_000)
        print(f"  Filled real field #weekly-list-date-created-from with {date_from_str!r}")
    except Exception as e:
        print(f"  ⚠ Could not fill date-created-from: {e}")

    try:
        to_field = page.locator("#weekly-list-date-created-to")
        await to_field.fill(date_to_str, timeout=8_000)
        print(f"  Filled real field #weekly-list-date-created-to with {date_to_str!r}")
    except Exception as e:
        print(f"  ⚠ Could not fill date-created-to: {e}")

    try:
        search_btn = page.locator("button", has_text="Search")
        await search_btn.first.click(timeout=10_000)
        print(f"  Clicked real 'Search' button")
    except Exception as e:
        print(f"  ⚠ Could not click Search: {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(2)  # real, deliberate pause for any client-side
                             # results rendering to finish

    title = await page.title()
    print(f"  Real page title after search: {title!r}")

    html = await page.content()
    out_html = f"/tmp/statmap_results_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/statmap_results_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:2000]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 2000 chars): {body_text!r}")

    # Real, direct check for a results table or any results-shaped
    # content, so the summary below is honest rather than assumed
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"\n  Real <table> elements found: {len(tables)}")
    if tables:
        rows = tables[0].find_all("tr")
        print(f"  First table has {len(rows)} rows")
        if rows:
            print(f"  Header row: {rows[0]}")
            if len(rows) > 1:
                print(f"  First data row: {rows[1]}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] statmap Weekly Lists "
          f"SEARCH RESULTS recon (round 3) — {len(TARGETS)} councils\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for name, url in TARGETS:
            await recon_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)
    print("Download the workflow artifact and read the saved HTML/screenshots")
    print("directly — this is the real results structure to build a parser")
    print("against.")


if __name__ == "__main__":
    asyncio.run(main())
