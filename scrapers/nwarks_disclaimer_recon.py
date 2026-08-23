#!/usr/bin/env python3
"""
PlanFind — North Warwickshire disclaimer-gate recon (2026-08-23).

Real, confirmed via search_advanced_family_recon.py: navigating
directly to /Search/Advanced redirects to a real disclaimer page
first (confirmed URL: /Disclaimer?returnURL=%2FSearch%2FAdvanced).
This is otherwise the same "Search/Advanced" platform family already
built for Westmorland and Furness, Cherwell, Wychavon, Malvern Hills —
North Warwickshire was deliberately excluded from that build pending
this real recon of its one genuinely different step.
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

BASE_URL = "https://planning.northwarks.gov.uk"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] North Warwickshire disclaimer-gate recon\n")

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

        title = await page.title()
        print(f"Real page title: {title!r}")
        print(f"Real final URL: {page.url}")

        html = await page.content()
        out_html = "/tmp/nwarks_disclaimer_recon.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/nwarks_disclaimer_recon.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"Saved: {out_html}, {out_png}\n")

        # Real, direct dump of every real button/link/form element on
        # the disclaimer page — no assumptions about what the accept
        # mechanism looks like
        print("Real buttons/inputs on the disclaimer page:")
        try:
            buttons = page.locator("button, input[type='submit'], input[type='button']")
            count = await buttons.count()
            for i in range(count):
                el = buttons.nth(i)
                text = (await el.inner_text()) or (await el.get_attribute("value")) or ""
                el_id = await el.get_attribute("id") or ""
                if text.strip():
                    print(f"  <button/input> text={text.strip()!r} id={el_id!r}")
        except Exception as e:
            print(f"  ⚠ button dump error: {e}")

        print("\nReal links on the disclaimer page:")
        try:
            links = page.locator("a")
            count = await links.count()
            for i in range(min(count, 20)):
                el = links.nth(i)
                text = (await el.inner_text()).strip()
                href = await el.get_attribute("href") or ""
                if text:
                    print(f"  {text!r} -> {href[:100]}")
        except Exception as e:
            print(f"  ⚠ link dump error: {e}")

        print("\nReal checkboxes on the disclaimer page:")
        try:
            checkboxes = page.locator("input[type='checkbox']")
            count = await checkboxes.count()
            for i in range(count):
                el = checkboxes.nth(i)
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                print(f"  name={name!r} id={el_id!r}")
        except Exception as e:
            print(f"  ⚠ checkbox dump error: {e}")

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:1500]
        except Exception:
            pass
        print(f"\nReal visible body text (first 1500 chars): {body_text!r}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
