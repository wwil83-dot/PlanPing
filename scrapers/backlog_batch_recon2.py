#!/usr/bin/env python3
"""
PlanFind — backlog batch recon 2 (2026-08-30, revised).

Targets below are from the user's OWN manually-verified recon (more
reliable than web search — an earlier version of this script used
web-search-derived URLs, some of which turned out wrong, e.g. Fylde:
web search found an old/decommissioned Idox instance at
www3.fylde.gov.uk; the real live one is pa.fylde.gov.uk, a bespoke
platform).

Gedling confirmed genuinely on Idox
(pawam.gedling.gov.uk/online-applications) — not recon'd here, goes
straight into idox_councils.py instead.

West Dunbartonshire gets TWO targets: the weekly-list picker page
(dcweekly_listx.asp) and a direct date-range results URL
(dcdisplayinitial.asp) using the exact query-string shape the user
already confirmed works manually
(WeekEnding/vDateRcvFr/vDateRcvTo/vWARDSelect/Submit2) — worth seeing
both the picker and the real results structure in one pass.
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
    ("West Dunbartonshire Council — weekly list picker",
     "https://apps.west-dunbarton.gov.uk/dcweekly_listx.asp"),
    ("West Dunbartonshire Council — direct date-range results",
     "https://apps.west-dunbarton.gov.uk/dcdisplayinitial.asp"
     "?WeekEnding=04%2F07%2F2026%7C10%2F07%2F2026"
     "&vDateRcvFr=01%2F08%2F2026&vDateRcvTo=30%2F08%2F2026"
     "&vWARDSelect=&Submit2=Search"),
    ("Redcar and Cleveland Borough Council",
     "https://planning.redcar-cleveland.gov.uk/Search/Planning/Advanced"),
    ("Ribble Valley Borough Council — weekly PDF list index",
     "https://www.ribblevalley.gov.uk/downloads/download/235/weekly-lists-of-planning-applications-registered"),
    ("Fylde Council",
     "https://pa.fylde.gov.uk/Search/Advanced"),
    ("Kirklees Council",
     "https://www.kirklees.gov.uk/beta/planning-applications/search-for-planning-applications/default.aspx?advanced_search=true"),
    ("Rotherham Metropolitan Borough Council — weekly list",
     "https://planning.rotherham.gov.uk/weeklylistapp.asp"),
    ("North Lincolnshire Council",
     "https://apps.northlincs.gov.uk/"),
    ("Telford and Wrekin Council",
     "https://secure.telford.gov.uk/planningsearch/"),
    ("South Derbyshire District Council",
     "https://planning.southderbyshire.gov.uk/"),
    ("Amber Valley Borough Council",
     "https://www.ambervalley.gov.uk/planning/development-management/view-a-planning-application/"),
    ("Boston Borough Council",
     "https://www.boston.gov.uk/planningapplicationsearch"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("(", "").replace(")", "")


async def dump_form_fields(page):
    print(f"\n  Real form fields found on this page:")
    try:
        inputs = page.locator("input")
        count = await inputs.count()
        for i in range(min(count, 30)):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                if itype.lower() not in ("hidden",):
                    print(f"    <input> type={itype!r} name={name!r} id={el_id!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ input dump error: {e}")

    try:
        iframes = page.locator("iframe")
        icount = await iframes.count()
        for i in range(min(icount, 5)):
            el = iframes.nth(i)
            try:
                src = await el.get_attribute("src") or ""
                if src:
                    print(f"    <iframe> src={src!r}")
            except Exception:
                pass
    except Exception:
        pass


async def recon_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error (real timeout/block signature): {e}")
        await context.close()
        return

    print(f"  Real HTTP status: {response.status if response else None}")
    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/backlog2_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/backlog2_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_form_fields(page)

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1200]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1200 chars): {body_text!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Backlog batch recon 2 "
          f"— {len(TARGETS)} candidates\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, url in TARGETS:
            await recon_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
