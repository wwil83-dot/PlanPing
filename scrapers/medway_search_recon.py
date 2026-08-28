#!/usr/bin/env python3
"""
PlanFind — Medway (Open Digital Planning) search recon (2026-08-28).

Real, confirmed via earlier oneoff_batch_recon.py: 10 real application
cards already appear directly on the landing page ("Recently published
applications"), a real GET form (action=/medway/search-form) with
confirmed params council=medway&query= — confirming this is a shared,
multi-council platform (same category as OcellaWeb), not Medway-
specific software. Testing whether an empty-query search reveals more
than the landing page (matching Herefordshire's own "empty search =
show all" pattern), and checking for real pagination.
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


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Medway search recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        # Real, confirmed form: method=get action=/medway/search-form,
        # params council=medway&query= — testing a direct empty-query
        # URL first
        url = "https://planningregister.org/medway/search-form?council=medway&query="
        print(f"Testing real direct URL: {url}\n")

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        print(f"Real HTTP status: {response.status if response else None}")
        print(f"Real final URL: {page.url}\n")

        try:
            accept_btn = page.get_by_text("Accept analytics cookies", exact=True)
            if await accept_btn.count() > 0:
                await accept_btn.first.click(timeout=5_000)
                await asyncio.sleep(1)
        except Exception:
            pass

        from bs4 import BeautifulSoup
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("article", class_="dpr-application-card")
        print(f"Real application cards found: {len(cards)}")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:2500]
        except Exception:
            pass
        print(f"\nReal visible body text (first 2500 chars): {body_text!r}\n")

        # Real, direct check for any pagination-suggestive links
        pagination_hints = await page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            const hits = [];
            for (const a of links) {
                const t = a.textContent.trim();
                if (/next|page \\d|more|previous/i.test(t) && t.length < 30) {
                    hits.push({text: t, href: a.getAttribute('href')});
                }
            }
            return hits;
        }""")
        print(f"Real pagination-suggestive links found: {pagination_hints}\n")

        # Real, full HTML of the first card for exact structure
        if cards:
            print(f"Real first card structure:\n{cards[0].prettify()[:2000]}")

        out_html = "/tmp/medway_search_recon.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/medway_search_recon.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"\nSaved: {out_html}, {out_png}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
