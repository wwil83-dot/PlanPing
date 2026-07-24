#!/usr/bin/env python3
"""
PlanFind — Civica reconnaissance tool (first version, 2026-07-21).

Same "get real evidence before writing scraper code" discipline as
idox_form_recon.py and arcus_recon.py before it. We have never inspected
Civica's real markup — everything so far comes from search-result
snippets, not direct evidence. This tool loads all 3 known-real Civica
councils in one run (sequentially, not concurrent, so there's no
interleaving confusion in the output) and dumps generic, exploratory
evidence rather than presuming a specific structure, since we don't yet
know whether Civica is a modern SPA (like Arcus's Salesforce Lightning)
or a traditional server-rendered form (which the "Search/BackToSearch"-
style URLs found so far suggest is more likely — classic ASP.NET-style
naming, not a JS framework).

CONFIRMED REAL TARGETS (updated 2026-07-24, round 3):
  - Harrow: planningsearch.harrow.gov.uk/planning/search-applications
    (NEW candidate — a past session's idox_councils.py comment flagged
    this as "a custom system", not identifying the vendor further. The
    URL path shape (/planning/search-applications) matches Civica
    Portal360 exactly — same shape as both Waverley and St Albans. Not
    yet confirmed, this recon run is what settles it.)

Waverley and West Northamptonshire dropped from this round — Waverley
is a settled, confirmed real Incapsula WAF block (no further recon
needed), and West Northamptonshire is deprioritized after two stuck
attempts at its disclaimer interstitial, now that Harrow is a more
promising real lead.
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
    ("London Borough of Harrow",
     "https://planningsearch.harrow.gov.uk/planning/search-applications", "civica_portal360_suspected"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace(",", "")


async def recon_one(pw, name: str, url: str, product: str):
    print(f"\n{'=' * 70}")
    print(f"CIVICA RECON: {name}  (suspected product: {product})")
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

    # DISCLAIMER CLICK-THROUGH (2026-07-23): found via real evidence on
    # West Northamptonshire's real register — the first recon run landed
    # on a "Copyright & Disclaimer" interstitial page (confirmed via its
    # real page title), not the actual search form, which explained why
    # only 2 generic inputs and zero weekly-list matches were found. Many
    # traditional (non-SPA) council planning registers gate the real
    # search form behind a one-time terms acceptance click. Try common
    # phrasings; harmless no-op if this council doesn't have one.
    clicked_through = False
    for accept_text in ["I agree", "I Agree", "Accept", "Continue", "I understand",
                         "I Understand", "Agree", "OK", "Proceed"]:
        try:
            btn = page.get_by_text(accept_text, exact=False)
            if await btn.count() > 0:
                await btn.first.click(timeout=5_000, force=True)
                clicked_through = True
                print(f"  Clicked through disclaimer via text: {accept_text!r}")
                await asyncio.sleep(2)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeout:
                    pass
                break
        except Exception:
            continue
    if not clicked_through:
        print("  (no disclaimer click-through found or needed — continuing with page as-is)")

    title = await page.title()
    html = await page.content()
    print(f"  Real page title: {title!r}")
    print(f"  HTML length: {len(html)} chars")

    # Generic, exploratory structure detection — no assumptions about
    # what Civica's real markup looks like, since we've never seen it.
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

    # Check for anything CSV-export-flavoured, and any weekly-list-style
    # links, since we don't yet know if Civica offers either.
    csv_hits = page.locator("text=/csv|export|download/i")
    csv_count = await csv_hits.count()
    print(f"\n  Elements matching 'CSV/export/download' text: {csv_count}")
    for i in range(min(csv_count, 5)):
        try:
            text = await csv_hits.nth(i).inner_text()
            print(f"    [{i}] {text!r}")
        except Exception:
            pass

    weekly_hits = page.locator("text=/weekly/i")
    weekly_count = await weekly_hits.count()
    print(f"  Elements matching 'weekly' text: {weekly_count}")
    for i in range(min(weekly_count, 5)):
        try:
            text = await weekly_hits.nth(i).inner_text()
            print(f"    [{i}] {text!r}")
        except Exception:
            pass

    # Visible body text snippet — catches WAF/error pages directly, same
    # as every other diagnostic this session.
    try:
        body_text = await page.locator("body").inner_text()
        snippet = " ".join(body_text.split())[:400]
        print(f"\n  Visible body text (first 400 chars): {snippet!r}")
    except Exception as e:
        print(f"\n  (couldn't extract body text: {e})")

    out_html = f"/tmp/civica_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/civica_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
        print(f"\n  Saved: {out_html}, {out_png}")
    except Exception as e:
        print(f"\n  Saved: {out_html} (screenshot failed: {e})")

    await browser.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Civica recon — "
          f"{len(TARGETS)} known real targets, run sequentially\n")

    async with async_playwright() as pw:
        for name, url, product in TARGETS:
            await recon_one(pw, name, url, product)

    print(f"\n{'=' * 70}")
    print("Civica recon complete. Download the workflow artifact and read")
    print("both the HTML and screenshots for each council before writing")
    print("any scraper code — same discipline as idox_form_recon.py and")
    print("arcus_recon.py before it.")


if __name__ == "__main__":
    asyncio.run(main())
