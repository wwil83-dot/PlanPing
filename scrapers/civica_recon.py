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

CONFIRMED REAL TARGETS (updated 2026-07-23, round 2):
  - Waverley Borough Council: planning360.waverley.gov.uk:4443/planning
    (CONFIRMED "Portal360" — Civica's own case study names this exact
    product for this exact council: "they simply click on the 'Planning'
    tab... to access Civica's intuitive Portal360 - Planning portal."
    Real, live, working — screenshot shows dated "Weekly list of
    decisions" links (5 July to 11 July, 28 June to 4 July, etc.), a
    "Weekly list of applications" link, and Advanced search. This
    council was previously in manual_link (its OLD stored portal_url is
    dead) — it has since migrated to this new, real, working portal we
    never knew about until a user reported the manual_link not working
    and investigated further.)
  - St Albans: planningapplications.stalbans.gov.uk/planning
    (Same Portal360 product — confirmed via matching page structure:
    "Search applications · Advanced search · Planning Help · Do I Need
    Planning Permission..." — identical menu shape to Waverley's real
    page. HONEST CAVEAT: an earlier, vaguer piece of research today
    mentioned St Albans alongside "Agile Applications" as a supplier —
    not necessarily a contradiction, could easily be the same
    two-systems-in-parallel pattern already confirmed today for Erewash/
    Wrexham (a different vendor for a different service, e.g. Building
    Control). Not yet confirmed as Idox or already covered — worth
    checking against idox_councils.py before assuming it's new, which
    this recon run's real evidence will settle either way.)
  - West Northamptonshire Council: wnc.planning-register.co.uk
    (STILL UNRESOLVED from round 1 — real, live register confirmed, but
    every recon attempt has landed on a "Copyright & Disclaimer"
    interstitial page, never the actual search form behind it. A generic
    "Accept" click-through didn't move past it — the real HTML/screenshot
    from round 1 needs a closer look to find the real way through, rather
    than guessing another button label blind.)

Erewash and Wrexham are DELIBERATELY REMOVED from this round — round 1
found real Arcus registers for both on different domains entirely
(planning.erewash.gov.uk, register.wrexham.gov.uk), already added to
arcus_councils.py and confirmed working in production. Whatever Civica
branding exists for them is evidently a different system (likely
Building Control specifically), not relevant to a Civica planning
investigation.
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
    ("Waverley Borough Council",
     "https://planning360.waverley.gov.uk:4443/planning", "civica_portal360"),
    ("St Albans",
     "https://planningapplications.stalbans.gov.uk/planning", "civica_portal360"),
    ("West Northamptonshire Council",
     "https://wnc.planning-register.co.uk/", "unknown_vendor"),
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
