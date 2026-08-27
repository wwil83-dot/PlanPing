#!/usr/bin/env python3
"""
PlanFind — Greater Cambridge shared-portal council-separation check
(2026-08-27).

Real, confirmed: both Cambridge City's and South Cambridgeshire's old,
separate Idox URLs are genuinely dead (DNS failure and timeout
respectively) — the real, current system for both is this one shared
portal. Before updating either existing config entry, checking
whether the Advanced Search page has a real way to filter/identify
which of the 2 councils a given result belongs to, since they need to
stay in two separate database rows, not get merged into one.
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

URL = "https://applications.greatercambridgeplanning.org/online-applications/search.do?action=advanced"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Greater Cambridge council-separation check\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        print(f"Real final URL: {page.url}")
        title = await page.title()
        print(f"Real page title: {title!r}\n")

        # Real, direct check for any select/dropdown that might filter
        # by council/authority
        selects = page.locator("select")
        count = await selects.count()
        print(f"Real <select> dropdowns found: {count}")
        for i in range(count):
            el = selects.nth(i)
            name = await el.get_attribute("name") or ""
            el_id = await el.get_attribute("id") or ""
            options = await el.locator("option").all_text_contents()
            print(f"\n  <select> name={name!r} id={el_id!r}")
            print(f"    Real options: {options[:20]}")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:2000]
        except Exception:
            pass
        print(f"\nReal visible body text (first 2000 chars): {body_text!r}")

        out_html = "/tmp/gcambridge_separation_check.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(await page.content())
        out_png = "/tmp/gcambridge_separation_check.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"\nSaved: {out_html}, {out_png}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("CHECK COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
