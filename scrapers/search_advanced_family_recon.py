#!/usr/bin/env python3
"""
PlanFind — "Search/Advanced" family recon: Cherwell, North Warwickshire,
Wychavon, Malvern Hills (2026-08-22).

Real, confirmed hypothesis: these 4 councils' URLs were flagged in an
earlier council-search batch as sharing the exact same "/Search/
Advanced" URL path as Eden/South Lakeland (Westmorland and Furness
Council), whose real interaction pattern took 8 recon rounds to fully
nail down. Testing whether that same pattern — real field ids
DateReceivedFrom/DateReceivedTo, a plain "Search" button, a 4-column
results table (Application Number | Location | Proposal | Status), and
a "Next" link with a data-ajax-target attribute for pagination — holds
for all 4, or whether any genuinely differ, MUCH faster this time since
we already know exactly what to check for rather than exploring blind.
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

# Real, exact URLs the user originally supplied. North Warwickshire
# deliberately excluded from this retest — its real disclaimer-gate
# redirect is a confirmed, genuine structural difference, not an
# artifact of the button-selector bug fixed above.
TARGETS = [
    ("Cherwell District Council", "https://planningregister.cherwell.gov.uk"),
    ("Wychavon District Council", "https://plan.wychavon.gov.uk"),
    ("Malvern Hills District Council", "https://plan.malvernhills.gov.uk"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def get_refs(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None, [], 0
    # Real, defensive check: look for a real results-shaped table
    # (one with a real <a> link in its first data-row cell) among ALL
    # tables on the page, not just assuming the first one is right —
    # confirmed necessary this round, given the search form's own
    # category-selector table keeps being picked up as if it were
    # results.
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [td.get_text(strip=True) for td in rows[0].find_all("td")]
        refs = []
        for r in rows[1:]:
            cells = r.find_all("td")
            if cells:
                a = cells[0].find("a")
                if a:
                    refs.append(a.get_text(strip=True))
        if refs:
            return header, refs, len(tables)
    # No table with real refs found — return the first table's header
    # anyway, for visibility into what WAS found
    rows = tables[0].find_all("tr")
    header = [td.get_text(strip=True) for td in rows[0].find_all("td")] if rows else []
    return header, [], len(tables)


async def recon_one(browser, name: str, base_url: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {name}")
    print(f"URL: {base_url}/Search/Advanced")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(f"{base_url}/Search/Advanced", wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    # Real, direct check for the exact confirmed field ids
    has_from = await page.locator("#DateReceivedFrom").count() > 0
    has_to = await page.locator("#DateReceivedTo").count() > 0
    print(f"  #DateReceivedFrom present: {has_from}")
    print(f"  #DateReceivedTo present: {has_to}")

    if not (has_from and has_to):
        print(f"  ⚠ Real field ids DON'T match Eden/South Lakeland's confirmed "
              f"pattern — this council genuinely differs, needs its own "
              f"dedicated recon, not a quick add.")
        # Real, direct dump of whatever date-shaped fields DO exist,
        # so the real difference is visible rather than just "it failed"
        try:
            inputs = page.locator("input")
            count = await inputs.count()
            print(f"  Real input fields found instead:")
            for i in range(count):
                el = inputs.nth(i)
                name_attr = await el.get_attribute("name") or ""
                id_attr = await el.get_attribute("id") or ""
                if "date" in name_attr.lower() or "date" in id_attr.lower():
                    print(f"    name={name_attr!r} id={id_attr!r}")
        except Exception:
            pass
        await context.close()
        return

    # Real, confirmed flow — fill, submit, check results
    today = date.today()
    start = today - timedelta(days=30)
    try:
        await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
        await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)

        # REAL FIX: the previous version's generic
        # "button:has-text('Search')" selector grabbed the site's own
        # header search TOGGLE button (confirmed directly on Cherwell:
        # class="site-header__search-button", completely unrelated to
        # the real Advanced Search form) — same category of substring/
        # generic-text-match trap already hit once this session
        # (statmap's "Property Search" vs "Search"). Scoping the click
        # specifically to a button INSIDE the real form element that
        # contains the date fields we just filled, not the whole page.
        form_with_dates = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
        search_btn = form_with_dates.locator("button:has-text('Search')")
        if await search_btn.count() == 0:
            # Real, defensive fallback — some of these forms may use a
            # real <input type=submit> instead of a <button>
            search_btn = form_with_dates.locator("input[type='submit']")
        await search_btn.first.click(timeout=5_000)
    except Exception as e:
        print(f"  ⚠ Could not fill/submit real search: {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)

    print(f"  Real URL after search: {page.url}")
    html = await page.content()

    # REAL, DIRECT EVIDENCE — save full HTML and a screenshot so the
    # actual page state can be seen directly, rather than continuing
    # to guess from incomplete table/URL signals after two rounds of
    # inconclusive results.
    out_html = f"/tmp/search_advanced_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/search_advanced_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    header, refs, total_tables = get_refs(html)
    print(f"  Real total <table> elements on page: {total_tables}")
    print(f"  Real table header: {header}")
    print(f"  Real refs found on page 1: {len(refs)}: {refs[:5]}")

    body_text = ""
    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        pass
    import re
    total_match = re.search(r"\((\d+)\)", body_text)
    print(f"  Real total count text found: {total_match.group(0) if total_match else 'NONE'}")

    # Real, direct check for the confirmed Next-link pagination pattern
    next_info = await page.evaluate("""() => {
        const links = document.querySelectorAll('a');
        for (const a of links) {
            if (a.textContent.trim() === 'Next') {
                return {found: true, ajaxTarget: a.getAttribute('data-ajax-target'), href: a.getAttribute('href')};
            }
        }
        return {found: false};
    }""")
    print(f"  Real 'Next' link: {next_info}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] 'Search/Advanced' family recon "
          f"— {len(TARGETS)} candidate councils\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, base_url in TARGETS:
            await recon_one(browser, name, base_url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)
    print("For any council where the field ids, table header, and Next-link")
    print("pattern all matched exactly — it should be a quick, safe add to")
    print("esl_scraper.py. Anything that genuinely differs needs its own")
    print("dedicated recon before being added.")


if __name__ == "__main__":
    asyncio.run(main())
