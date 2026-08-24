#!/usr/bin/env python3
"""
PlanFind — Eden/South Lakeland detail page recon (2026-08-24).

Real, confirmed gap: esl_scraper.py never captures a date at all — the
results list's real confirmed columns are Application Number |
Location | Proposal | Status, genuinely no date column. Checking
whether the real, permanent detail page (/Planning/Display/{reference})
has a genuine date field worth extracting, before deciding whether
visiting every application's own detail page (one extra real page load
per application) is actually worth the real, added cost.

Real, already-confirmed reference reused directly from earlier tonight's
evidence (wandf_r3_esl_results.html) — planning references don't
expire, so this should still resolve.
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

REAL_URL = "https://planningregister.westmorlandandfurness.gov.uk/Planning/Display/2026/1595/FPA"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Eden/South Lakeland detail page recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            response = await page.goto(REAL_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        print(f"Real HTTP status: {response.status if response else None}")
        title = await page.title()
        print(f"Real page title: {title!r}")
        print(f"Real final URL: {page.url}\n")

        html = await page.content()
        out_html = "/tmp/esl_detail_page.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/esl_detail_page.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"Saved: {out_html}, {out_png}\n")

        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass

        print(f"Real visible body text (first 3000 chars):\n{body_text[:3000]!r}\n")

        # Real, direct check for anything date-shaped anywhere on the
        # page — labels, values, table rows
        import re
        date_pattern = re.compile(r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b")
        date_matches = date_pattern.findall(body_text)
        print(f"Real date-shaped strings found anywhere on the page: {date_matches}")

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
