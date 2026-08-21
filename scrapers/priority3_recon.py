#!/usr/bin/env python3
"""
PlanFind — Priority 3 recon: agileapplications.co.uk family (Middlesbrough,
Flintshire, Cannock) + statmap.co.uk/horizoNext family (West Lindsey, East
Staffordshire) (2026-08-21).

TWO GENUINELY DIFFERENT PLATFORMS, real evidence for each kept separate:

1. agileapplications.co.uk — real URLs already supplied directly by the
   user's own research, not guessed:
     https://planning.agileapplications.co.uk/flintshire/search-applications/results
       ?criteria={"status":"registered","registrationDateFrom":"...","registrationDateTo":"..."}
       &page=1
   Same real pattern confirmed for Cannock. Middlesbrough flagged
   separately by the user as "VERY BESPOKE WEBSITE AND VERY SLOW" — real
   council-slug pattern assumed the same, worth confirming directly
   rather than assuming.

   REAL HYPOTHESIS WORTH TESTING CHEAPLY FIRST: the URL shape (a
   /results endpoint taking a JSON "criteria" parameter) looks like it
   could be a genuine backend API call, not just a browser-rendered
   page URL — similar to how NI Planning Portal turned out to be a
   clean, direct REST API needing no browser at all. Testing a PLAIN
   httpx GET first, before assuming Playwright/JS execution is needed —
   cheaper and faster if it works, and tells us something real either
   way.

2. statmap.co.uk/horizoNext — no pre-mapped URL structure at all, only
   base portal URLs. Genuinely needs full UI discovery from scratch,
   same as Northgate servlet family's original recon — real form
   fields, real search interaction, real results structure, nothing
   assumed.
"""
import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
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
HTTP_HEADERS = {
    "User-Agent": CONTEXT_OPTIONS["user_agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


# ---------------------------------------------------------------------------
# Part 1 — agileapplications.co.uk family
# ---------------------------------------------------------------------------
AGILE_COUNCILS = [
    ("Flintshire County Council", "flintshire"),
    ("Cannock Chase District Council", "cannock"),
    ("Middlesbrough Council", "middlesbrough"),
]


def _agile_url(council_slug: str, days_back: int = 14) -> str:
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=days_back)
    criteria = (
        '{"status":"registered",'
        f'"registrationDateFrom":"{start.isoformat()}T00:00:00+01:00",'
        f'"registrationDateTo":"{today.isoformat()}T23:59:59+01:00"}}'
    )
    return (f"https://planning.agileapplications.co.uk/{council_slug}"
            f"/search-applications/results?criteria={quote(criteria)}&page=1")


async def recon_agile_plain_http(name: str, council_slug: str):
    url = _agile_url(council_slug)
    print(f"\n{'=' * 70}")
    print(f"AGILEAPPLICATIONS RECON (plain HTTP, no browser): {name}")
    print(f"URL: {url}")
    print("=" * 70)

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url, headers=HTTP_HEADERS)
    except Exception as e:
        print(f"  ⚠ Request failed: {e}")
        return {"plain_http_worked": False}

    print(f"  HTTP status: {r.status_code}")
    print(f"  Content-Type: {r.headers.get('content-type', '?')}")
    print(f"  Real content length: {len(r.text):,} chars")

    body_lower = r.text.lower()
    looks_like_json = r.text.strip().startswith(("{", "["))
    # REAL FIX, based on actual evidence from the first run: a 200
    # status + real content length is NOT proof of real data — this
    # platform serves an empty React/Angular SPA shell (confirmed via
    # the real CSP headers referencing azurewebsites.net) regardless
    # of the URL's own criteria, and only a real browser executing that
    # SPA's JS actually reaches real application data. Checking for a
    # real, distinctive marker from the SPA's own confirmed rendered
    # output ("results" count text) rather than trusting status alone.
    has_real_data = "results" in body_lower and "citizen portal" not in body_lower[:200]
    print(f"  Looks like raw JSON: {looks_like_json}")
    print(f"  Contains real application data (not just the SPA shell): {has_real_data}")

    if r.status_code == 200 and len(r.text) > 200:
        out_path = f"/tmp/agile_recon_{slug(name)}_plain_http.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(r.text)
        print(f"  Saved: {out_path}")
        snippet = " ".join(r.text.split())[:1500]
        print(f"  Content snippet: {snippet!r}")

    return {"plain_http_worked": r.status_code == 200 and len(r.text) > 200,
            "looks_like_json": looks_like_json}


async def recon_agile_browser(browser, name: str, council_slug: str):
    url = _agile_url(council_slug)
    print(f"\n{'-' * 70}")
    print(f"AGILEAPPLICATIONS RECON (real browser): {name}")
    print(f"URL: {url}")
    print("-" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="networkidle", timeout=45_000)
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    print(f"  HTTP status: {response.status if response else None}")
    await asyncio.sleep(2)  # real, deliberate pause for any client-side
                             # rendering to finish, given no evidence
                             # yet on whether this is a SPA

    title = await page.title()
    print(f"  Real page title: {title!r}")

    html = await page.content()
    out_html = f"/tmp/agile_recon_{slug(name)}_browser.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/agile_recon_{slug(name)}_browser.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1500]
    except Exception:
        pass
    print(f"  Real visible body text (first 1500 chars): {body_text!r}")

    await context.close()


# ---------------------------------------------------------------------------
# Part 2 — statmap.co.uk/horizoNext family
# ---------------------------------------------------------------------------
STATMAP_COUNCILS = [
    ("West Lindsey District Council",
     "https://westlindsey-publicportal.statmap.co.uk/horizoNext/publicportal"),
    ("East Staffordshire Borough Council",
     "https://eaststaffs-publicportal.statmap.co.uk/horizoNext/publicportal"),
]


async def dump_form_fields(page):
    print(f"\n  Real form fields found on this page:")
    try:
        inputs = page.locator("input")
        count = await inputs.count()
        for i in range(count):
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
        selects = page.locator("select")
        count = await selects.count()
        for i in range(count):
            el = selects.nth(i)
            try:
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                opt_count = await el.locator("option").count()
                print(f"    <select> name={name!r} id={el_id!r} ({opt_count} options)")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ select dump error: {e}")

    try:
        buttons = page.locator("button")
        count = await buttons.count()
        for i in range(min(count, 15)):
            el = buttons.nth(i)
            try:
                text = await el.inner_text()
                el_id = await el.get_attribute("id") or ""
                if text.strip():
                    print(f"    <button> text={text.strip()!r} id={el_id!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ button dump error: {e}")


async def recon_statmap(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"STATMAP/HORIZONEXT RECON: {name}")
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
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    print(f"  HTTP status: {response.status if response else None}")
    await asyncio.sleep(1)

    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/statmap_recon_{slug(name)}_step1.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/statmap_recon_{slug(name)}_step1.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_form_fields(page)

    # Real, direct look at any visible tabs/nav (the user's own note
    # said "weekly searches" available — worth seeing what real real
    # navigation options exist without guessing)
    print(f"\n  Real visible links/tabs on this page:")
    try:
        links = page.locator("a")
        count = await links.count()
        seen = set()
        for i in range(min(count, 40)):
            el = links.nth(i)
            try:
                text = (await el.inner_text()).strip()
                if text and text not in seen and len(text) < 60:
                    seen.add(text)
                    print(f"    {text!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ link dump error: {e}")

    await context.close()


# ---------------------------------------------------------------------------
async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Priority 3 recon — "
          f"agileapplications.co.uk family + statmap.co.uk/horizoNext family\n")

    print("PART 1: agileapplications.co.uk — testing plain HTTP first, real "
          "hypothesis this might be a direct API call needing no browser\n")
    plain_http_results = {}
    for name, council_slug in AGILE_COUNCILS:
        plain_http_results[name] = await recon_agile_plain_http(name, council_slug)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"\nChromium launched: {browser.version}")

        print(f"\n{'#' * 70}")
        print("PART 1 (continued): agileapplications.co.uk — real browser check")
        print("#" * 70)
        for name, council_slug in AGILE_COUNCILS:
            await recon_agile_browser(browser, name, council_slug)

        print(f"\n{'#' * 70}")
        print("PART 2: statmap.co.uk/horizoNext family")
        print("#" * 70)
        for name, url in STATMAP_COUNCILS:
            await recon_statmap(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)
    print("Part 1 plain-HTTP results (does this platform need a browser at all?):")
    for name, result in plain_http_results.items():
        worked = result.get("plain_http_worked", False)
        is_json = result.get("looks_like_json", False)
        print(f"  {name}: plain HTTP worked={worked}, looks like raw JSON={is_json}")
    print("\nDownload the workflow artifact and read the saved HTML/screenshots")
    print("directly before writing any scraper code — same discipline as every")
    print("other platform this session.")


if __name__ == "__main__":
    asyncio.run(main())
