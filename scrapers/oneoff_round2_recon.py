#!/usr/bin/env python3
"""
PlanFind — Stratford/Herefordshire/Walsall round 2 recon (2026-08-27).

Real, confirmed from round 1's captured HTML:
  - Stratford: real "Validated"/"Decided"/"Accept" controls are
    <span> text nested inside real <button> elements, click handler
    bound via JS at runtime (E-Planning v2.11 framework) — using a
    text-based locator, same reliable pattern already proven for
    Barrow's "Close" button and ESL's "Next" link.
  - Herefordshire: real native type="date" fields (data-date-format=
    "Y/m/d" is just a display hint — the underlying native input
    always expects real ISO YYYY-MM-DD regardless) — same real fix
    already proven for North Warwickshire. 4 real "Search" buttons
    exist on the page; targeting the one scoped to the confirmed
    date-planning-from/to fields specifically, not the unrelated
    "Search area" map control.
  - Walsall: plain real text inputs, no id — only a name attribute
    (REGFROMDATE.MAINBODY.WPACIS.1 / REGTODATE.MAINBODY.WPACIS.1),
    targeting via a real [name=...] CSS selector.

Medway deliberately NOT included here — its real search form has no
date-range fields at all, just a single generic keyword box, and its
real "Recently published applications" list appears directly on the
landing page with no search needed. That's different enough
architecture to warrant its own separate, dedicated recon.
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


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def save_evidence(page, name: str):
    html = await page.content()
    out_html = f"/tmp/oneoff_r2_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/oneoff_r2_{slug(name)}.png"
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

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"\n  Real <table> elements found: {len(tables)}")
    for i, t in enumerate(tables):
        rows = t.find_all("tr")
        if len(rows) > 1:
            print(f"    Table {i}: {len(rows)} rows — header: {rows[0]}")
            print(f"    First data row: {rows[1]}")


async def recon_stratford(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Stratford-on-Avon District Council")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://apps.stratford.gov.uk/eplanningv2/Home/MonthlyList",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    try:
        accept_btn = page.get_by_text("Accept", exact=True)
        if await accept_btn.count() > 0:
            await accept_btn.first.click(timeout=5_000)
            await asyncio.sleep(1)
            print("  Clicked real 'Accept' cookie consent")
    except Exception as e:
        print(f"  ⚠ Could not accept cookies: {e}")

    try:
        validated_btn = page.get_by_text("Validated", exact=True)
        await validated_btn.first.click(timeout=8_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)
        print("  Clicked real 'Validated' button")
    except Exception as e:
        print(f"  ⚠ Could not click Validated: {e}")

    print(f"  Real URL after click: {page.url}")
    await save_evidence(page, "stratford")
    await context.close()


async def recon_herefordshire(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Herefordshire Council")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://www.herefordshire.gov.uk/planning-and-building-control/planning-search",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    try:
        accept_btn = page.get_by_text("Accept cookies", exact=True)
        if await accept_btn.count() > 0:
            await accept_btn.first.click(timeout=5_000)
            await asyncio.sleep(1)
            print("  Accepted real cookies")
    except Exception as e:
        print(f"  ⚠ Could not accept cookies: {e}")

    today = date.today()
    start = today - timedelta(days=30)

    try:
        for field_id, value in [("date-from", start.isoformat()), ("date-to", today.isoformat())]:
            await page.evaluate(
                """([id, val]) => {
                    const el = document.getElementById(id);
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }""",
                [field_id, value],
            )
        print(f"  Set real dates via JS: {start.isoformat()} to {today.isoformat()}")

        form_with_dates = page.locator("form").filter(has=page.locator("#date-from"))
        search_btn = form_with_dates.locator("button:has-text('Search')")
        await search_btn.first.click(timeout=5_000)
    except Exception as e:
        print(f"  ⚠ Could not fill/submit search: {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)

    print(f"  Real URL after search: {page.url}")
    await save_evidence(page, "herefordshire")
    await context.close()


async def recon_walsall(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Walsall Metropolitan Borough Council")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://planning.walsall.gov.uk/swift/apas/run/wphappcriteria.display",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    today = date.today()
    start = today - timedelta(days=30)

    try:
        await page.fill("[name='REGFROMDATE.MAINBODY.WPACIS.1']", start.strftime("%d/%m/%Y"), timeout=5_000)
        await page.fill("[name='REGTODATE.MAINBODY.WPACIS.1']", today.strftime("%d/%m/%Y"), timeout=5_000)
        print(f"  Filled real date fields: {start.strftime('%d/%m/%Y')} to {today.strftime('%d/%m/%Y')}")

        await page.locator("[name='SEARCHBUTTON.MAINBODY.WPACIS.1']").first.click(timeout=5_000)
    except Exception as e:
        print(f"  ⚠ Could not fill/submit search: {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)

    print(f"  Real URL after search: {page.url}")
    await save_evidence(page, "walsall")
    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Round 2 recon — Stratford/Herefordshire/Walsall\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await recon_stratford(browser)
        await recon_herefordshire(browser)
        await recon_walsall(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
