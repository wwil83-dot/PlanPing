#!/usr/bin/env python3
"""
PlanFind — existing-platform-family batch verification (2026-09-03).

6 candidates from the user's newest manual recon list, claimed to be
on platforms this project ALREADY has working scrapers for
(getapplications_scraper.py, arcus_scraper.py, agileapplications_
scraper.py). "Same platform type" is NOT the same as "confirmed
working URL" — Fylde was wrongly assumed to be Idox by web search
earlier this session, and Kirklees had a similar wrong assumption in
the original seed list. Verifying each before any get added to a live
scraper config.

Real, confirmed-real URL for the SAME platform, used as a positive
control to compare real markers against:
  - getapplications: Liverpool (fa=getApplications) — already live
  - Arcus: uses a Salesforce Experience Cloud community, real marker
    is "Arcus" branding / "Public Register" text
  - agileapplications: uses planning.agileapplications.co.uk/<slug>/
    search-applications/ — real marker is "agileapplications" branding

Candidates:
  - Harrow (getapplications family) — URL uses fa=search, NOT the
    usual fa=getApplications query param seen elsewhere in this
    family — worth confirming this still behaves the same way.
  - Blaenau Gwent (getapplications family) — standard fa=getApplications
    pattern, matches Liverpool's shape.
  - Milton Keynes (Arcus) — standard /pr/s/register-view pattern,
    matches this project's existing confirmed Arcus councils.
  - Wiltshire (possibly Arcus) — different URL shape (/pr3/s/
    be-register-view vs /pr/s/register-view) — could be a genuinely
    different Salesforce community deployment, not simply "the same."
  - Pembrokeshire (agileapplications) — standard shape, matches
    Flintshire/Cannock/Middlesbrough.
  - Slough (agileapplications) — user's own note: "different... manual
    input rather than from-to dates with drop down boxes" — flagged as
    possibly a different UI/interaction pattern even if same backend.
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

TARGETS = [
    ("Harrow (getapplications family)",
     "https://planningsearch.harrow.gov.uk/planning/index.html?fa=search"),
    ("Blaenau Gwent (getapplications family)",
     "https://developmentservices.blaenau-gwent.gov.uk/planning/index.html?fa=getApplications"),
    ("Milton Keynes (Arcus)",
     "https://www.be.milton-keynes.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register"),
    ("Wiltshire (possibly Arcus)",
     "https://development.wiltshire.gov.uk/pr3/s/be-register-view"),
    ("Pembrokeshire (agileapplications)",
     "https://planning.agileapplications.co.uk/pembrokeshire/search-applications/"),
    ("Slough (agileapplications, flagged as different UI)",
     "https://planning.agileapplications.co.uk/slough/search-applications/"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace(",", "").replace("/", "_")


async def recon_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error (real timeout/block signature): {type(e).__name__}: {e!r}")
        await context.close()
        return {"name": name, "verdict": "NAV_ERROR"}

    status = response.status if response else None
    title = await page.title()
    final_url = page.url

    print(f"  Real HTTP status: {status}")
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {final_url}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:800]
    except Exception:
        pass
    print(f"  Real body text (first 800 chars): {body_text!r}")

    # Dump real form fields — the actual thing needed to confirm the
    # interaction pattern matches (or doesn't) the already-proven
    # scraper's expectations.
    print(f"\n  Real form fields found:")
    for tag in ("input", "select"):
        els = page.locator(tag)
        count = await els.count()
        for i in range(min(count, 20)):
            el = els.nth(i)
            try:
                name_attr = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                itype = await el.get_attribute("type") or ""
                print(f"    <{tag}> type={itype!r} name={name_attr!r} id={el_id!r}")
            except Exception:
                pass

    out_html = f"/tmp/platform_verify_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(await page.content())
    out_png = f"/tmp/platform_verify_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"\n  Saved: {out_html}, {out_png}")

    await context.close()
    return {"name": name, "status": status, "title": title, "final_url": final_url}


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Existing-platform-family "
          f"batch verification — {len(TARGETS)} candidates\n")

    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, url in TARGETS:
            result = await recon_one(browser, name, url)
            results.append(result)
            await asyncio.sleep(3)  # light courtesy pacing between different domains

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
