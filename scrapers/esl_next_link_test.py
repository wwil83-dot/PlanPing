#!/usr/bin/env python3
"""
PlanFind — Eden/South Lakeland real 'Next' link confirmation (2026-08-22).

Real, confirmed: a plain <a> element with text "Next" exists on the
results page — missed by earlier class-name/href-pattern searches
since it apparently has no distinctive class or the checked patterns
didn't match. Getting its real href directly, then actually following
it to confirm it returns genuinely different applications, not just a
reload of page 1 (matching the session-state theory already confirmed
via the real POST -> redirect -> parameterless GET flow).
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


def get_refs(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    refs = []
    for r in table.find_all("tr")[1:]:
        cells = r.find_all("td")
        if cells:
            a = cells[0].find("a")
            if a:
                refs.append(a.get_text(strip=True))
    return refs


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Eden/South Lakeland real 'Next' link confirmation\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        await page.goto("https://planningregister.westmorlandandfurness.gov.uk/Search/Advanced",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        today = date.today()
        start = today - timedelta(days=30)
        await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
        await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)
        await page.locator("button:has-text('Search')").first.click(timeout=5_000)

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        html1 = await page.content()
        refs1 = get_refs(html1)
        print(f"Page 1: {len(refs1)} refs: {refs1}\n")

        # Real, direct extraction of the Next link's actual href
        next_href = await page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                if (a.textContent.trim() === 'Next') {
                    return {href: a.getAttribute('href'), outerHTML: a.outerHTML};
                }
            }
            return null;
        }""")
        print(f"Real 'Next' link found: {next_href}\n")

        if not next_href or not next_href.get("href"):
            print("⚠ No real href found on the Next link — likely JS-driven, "
                  "checking for a real onclick handler instead")
            onclick = await page.evaluate("""() => {
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    if (a.textContent.trim() === 'Next') {
                        return a.getAttribute('onclick');
                    }
                }
                return null;
            }""")
            print(f"Real onclick attribute: {onclick}")
            await context.close()
            await browser.close()
            return

        # Real, direct click on the actual Next element (safer than
        # manually constructing the URL, in case it's relative or
        # depends on real current session state)
        try:
            await page.get_by_text("Next", exact=True).first.click(timeout=5_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠ Could not click real Next link: {e}")
            await context.close()
            await browser.close()
            return

        print(f"Real URL after clicking Next: {page.url}\n")
        html2 = await page.content()
        refs2 = get_refs(html2)
        print(f"Page 2: {len(refs2)} refs: {refs2}\n")

        overlap = set(refs1) & set(refs2)
        print(f"Real overlap between page 1 and page 2: {len(overlap)} reference(s) — {overlap}")
        print(f"GENUINELY DIFFERENT PAGE: {len(overlap) == 0 and len(refs2) > 0}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("CONFIRMATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
