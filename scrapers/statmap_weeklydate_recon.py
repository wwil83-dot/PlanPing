#!/usr/bin/env python3
"""
PlanFind — statmap.co.uk/horizoNext, round 4: real weeklyListDate URL
test (2026-08-21).

Round 3 revealed the real "Weekly Lists" tab doesn't show application
data directly at all — it shows a short list of weekly REPORT entries,
each linking to a real, clean URL:
  /horizoNext/publicportal/planningapplications/?weeklyListDate=YYYY-MM-DD

That's a genuinely different, more promising lead than the "Download"
column initially suggested (a worry that this needed real PDF parsing)
— this URL points at /planningapplications/, the same real search area
confirmed as its own tab in round 1. Testing directly whether this URL
returns real, populated individual application data, the same way the
agileapplications.co.uk family did with a direct URL.
"""
import asyncio
from datetime import datetime, timezone

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

# Real, confirmed dates from round 3's actual results — using the same
# week that's already confirmed to exist for each council
TARGETS = [
    ("West Lindsey District Council",
     "https://westlindsey-publicportal.statmap.co.uk/horizoNext/publicportal/planningapplications/?weeklyListDate=2026-08-17"),
    ("East Staffordshire Borough Council",
     "https://eaststaffs-publicportal.statmap.co.uk/horizoNext/publicportal/planningapplications/?weeklyListDate=2026-08-17"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def recon_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"WEEKLYLISTDATE URL RECON: {name}")
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

    await asyncio.sleep(2)  # real, deliberate pause for any client-side
                             # rendering to finish

    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/statmap_weeklydate_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/statmap_weeklydate_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:2500]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 2500 chars): {body_text!r}")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("div", attrs={"role": "row"})
    print(f"\n  Real MUI DataGrid role=row elements found: {len(rows)}")
    if len(rows) > 1:
        print(f"  First real data row (row 2, since row 1 is the header):")
        print(f"  {str(rows[1])[:1000]}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] statmap weeklyListDate "
          f"URL recon (round 4) — {len(TARGETS)} councils\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for name, url in TARGETS:
            await recon_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
