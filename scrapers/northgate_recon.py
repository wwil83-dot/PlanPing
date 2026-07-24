#!/usr/bin/env python3
"""
PlanFind — Northgate PlanningExplorer reconnaissance tool (2026-07-24).

Same "get real evidence before writing scraper code" discipline as every
other recon this session. Northgate PlanningExplorer surfaced today as a
genuinely sizeable fourth vendor — the Planning Portal's own official
bulletin names it as one of the ~8 real UK council planning platforms,
and we've now confirmed 4 real users via direct research: Birmingham
(the UK's largest local authority by population), Islington, Runnymede,
and Tamworth. We've never inspected its real markup directly — everything
so far comes from search-result snippets and URL patterns, not direct
evidence.

Runs all 4 known-real councils sequentially (not concurrent, so there's
no interleaving confusion in the output), dumping generic, exploratory
evidence rather than presuming a specific structure. The .aspx extension
suggests a traditional ASP.NET server-rendered app (like Idox), not a
JS-heavy SPA (like Arcus) — worth confirming rather than assuming.
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
    ("Birmingham City Council",
     "https://eplanning.birmingham.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx"),
    ("Tamworth Borough Council",
     "http://planning.tamworth.gov.uk/northgate/planningexplorer/generalsearch.aspx"),
]
# Islington DELIBERATELY DROPPED (2026-07-24) — its own website confirms
# a system migration in April 2024: "In April 2024, we changed our
# planning application system. If you have saved or bookmarked an old
# link, use the button below and replace your bookmark with this link."
# The DNS failure wasn't a fluke, it's a genuinely dead, deprecated URL.
# Runnymede already confirmed working in the first round — not re-run
# here, this retry is specifically for the two ambiguous failures
# (Birmingham's 503, Tamworth's timeout) that showed no real block
# signature, worth a clean isolated retry before writing them off.


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace(",", "")


async def recon_one(pw, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"NORTHGATE RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(2)
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await browser.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(2)

    title = await page.title()
    html = await page.content()
    print(f"  Real page title: {title!r}")
    print(f"  HTML length: {len(html)} chars")

    forms = page.locator("form")
    form_count = await forms.count()
    print(f"\n  <form> elements found: {form_count}")

    inputs = page.locator("input")
    input_count = await inputs.count()
    print(f"  <input> elements found: {input_count}")
    for i in range(min(input_count, 20)):
        inp = inputs.nth(i)
        try:
            inp_type = await inp.get_attribute("type") or "(text)"
            inp_name = await inp.get_attribute("name") or "(no name)"
            inp_id = await inp.get_attribute("id") or "(no id)"
            print(f"    [{i}] type={inp_type!r} name={inp_name!r} id={inp_id!r}")
        except Exception:
            pass

    selects = page.locator("select")
    select_count = await selects.count()
    print(f"\n  <select> elements found: {select_count}")
    for i in range(min(select_count, 10)):
        sel = selects.nth(i)
        try:
            sel_name = await sel.get_attribute("name") or "(no name)"
            sel_id = await sel.get_attribute("id") or "(no id)"
            print(f"    [{i}] name={sel_name!r} id={sel_id!r}")
        except Exception:
            pass

    # Check for weekly-list-style links, CSV/export options
    weekly_hits = page.locator("text=/weekly/i")
    weekly_count = await weekly_hits.count()
    print(f"\n  Elements matching 'weekly' text: {weekly_count}")
    for i in range(min(weekly_count, 5)):
        try:
            text = await weekly_hits.nth(i).inner_text()
            print(f"    [{i}] {text!r}")
        except Exception:
            pass

    csv_hits = page.locator("text=/csv|export|download/i")
    csv_count = await csv_hits.count()
    print(f"  Elements matching 'CSV/export/download' text: {csv_count}")
    for i in range(min(csv_count, 5)):
        try:
            text = await csv_hits.nth(i).inner_text()
            print(f"    [{i}] {text!r}")
        except Exception:
            pass

    try:
        body_text = await page.locator("body").inner_text()
        snippet = " ".join(body_text.split())[:500]
        print(f"\n  Visible body text (first 500 chars): {snippet!r}")
    except Exception as e:
        print(f"\n  (couldn't extract body text: {e})")

    out_html = f"/tmp/northgate_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/northgate_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
        print(f"\n  Saved: {out_html}, {out_png}")
    except Exception as e:
        print(f"\n  Saved: {out_html} (screenshot failed: {e})")

    await browser.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Northgate recon — "
          f"{len(TARGETS)} known real targets, run sequentially\n")

    async with async_playwright() as pw:
        for name, url in TARGETS:
            await recon_one(pw, name, url)

    print(f"\n{'=' * 70}")
    print("Northgate recon complete. Download the workflow artifact and")
    print("read both the HTML and screenshots for each council before")
    print("writing any scraper code.")


if __name__ == "__main__":
    asyncio.run(main())
