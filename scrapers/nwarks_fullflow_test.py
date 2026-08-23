#!/usr/bin/env python3
"""
PlanFind — North Warwickshire full flow test (2026-08-23).

Real, confirmed disclaimer gate: a plain "Accept" button, no checkbox
required. Testing the full real flow after accepting — does it land
back on /Search/Advanced with the same confirmed field ids as the
other 4 "Search/Advanced" family councils, and does a real search
actually return genuine results?
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

BASE_URL = "https://planning.northwarks.gov.uk"


def get_refs(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].find_all("td") or rows[0].find_all("th")
        header = [c.get_text(strip=True) for c in header_cells]
        refs = []
        for r in rows[1:]:
            cells = r.find_all("td")
            if cells:
                a = cells[0].find("a")
                if a:
                    refs.append(a.get_text(strip=True))
        if refs:
            return header, refs
    return None, []


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] North Warwickshire full flow test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(f"{BASE_URL}/Search/Advanced", wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        print(f"Real URL before accepting: {page.url}")

        # Real, confirmed accept button
        try:
            accept_btn = page.get_by_role("button", name="Accept", exact=True)
            await accept_btn.first.click(timeout=5_000)
            print("Clicked real 'Accept' button")
        except Exception as e:
            print(f"⚠ Could not click Accept: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        print(f"Real URL after accepting: {page.url}\n")

        has_from = await page.locator("#DateReceivedFrom").count() > 0
        has_to = await page.locator("#DateReceivedTo").count() > 0
        has_planning_checkbox = await page.locator("#SearchPlanning").count() > 0
        print(f"#DateReceivedFrom present: {has_from}")
        print(f"#DateReceivedTo present: {has_to}")
        print(f"#SearchPlanning present: {has_planning_checkbox}")

        if not (has_from and has_to):
            print("\n⚠ Real field ids DON'T match the confirmed family pattern — "
                  "this council genuinely differs beyond just the disclaimer gate.")
            await browser.close()
            return

        today = date.today()
        start = today - timedelta(days=30)
        try:
            await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
            await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)

            if has_planning_checkbox:
                await page.locator("#SearchPlanning").check(timeout=3_000)
                print("Checked #SearchPlanning")

            form_with_dates = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
            search_btn = form_with_dates.locator("button:has-text('Search')")
            if await search_btn.count() == 0:
                search_btn = form_with_dates.locator("input[type='submit']")
            await search_btn.first.click(timeout=5_000)
        except Exception as e:
            print(f"⚠ Could not fill/submit search: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        print(f"\nReal URL after search: {page.url}")
        html = await page.content()

        out_html = "/tmp/nwarks_fullflow_results.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/nwarks_fullflow_results.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"Saved: {out_html}, {out_png}")

        header, refs = get_refs(html)
        print(f"\nReal table header: {header}")
        print(f"Real refs found on page 1: {len(refs)}: {refs[:5]}")

        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        import re
        total_match = re.search(r"\((\d+)\)", body_text)
        print(f"Real total count text: {total_match.group(0) if total_match else 'NONE'}")

        next_info = await page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                if (a.textContent.trim() === 'Next') {
                    return {found: true, ajaxTarget: a.getAttribute('data-ajax-target')};
                }
            }
            return {found: false};
        }""")
        print(f"Real 'Next' link: {next_info}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
