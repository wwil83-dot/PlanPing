#!/usr/bin/env python3
"""
PlanFind — Charnwood (Assure platform) recon (2026-08-28).

Real, confirmed URL directly from the person running this project:
https://planningexplorer.charnwood.gov.uk/Assure/ES/Presentation/Planning/OnLinePlanning/OnlinePlanningSearch

This confirms the ORIGINAL roadmap note ("Assure" platform) was
correct — an earlier web-search-based correction to a Northgate/
PlanningExplorerAA URL turned out to be a dead, unresolvable domain.
"Assure" is a genuinely new platform vendor never seen elsewhere in
this project — real, direct recon before assuming anything about its
structure.
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

URL = "https://planningexplorer.charnwood.gov.uk/Assure/ES/Presentation/Planning/OnLinePlanning/OnlinePlanningSearch"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Charnwood (Assure) recon\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            response = await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
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
        out_html = "/tmp/charnwood_assure_recon.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        out_png = "/tmp/charnwood_assure_recon.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
        except Exception:
            pass
        print(f"Saved: {out_html}, {out_png}\n")

        inputs = page.locator("input")
        count = await inputs.count()
        print(f"Real form fields found: {count}")
        for i in range(min(count, 30)):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                if itype.lower() not in ("hidden",):
                    print(f"  <input> type={itype!r} name={name!r} id={el_id!r}")
            except Exception:
                pass

        selects = page.locator("select")
        scount = await selects.count()
        print(f"\nReal select dropdowns: {scount}")
        for i in range(scount):
            el = selects.nth(i)
            name = await el.get_attribute("name") or ""
            el_id = await el.get_attribute("id") or ""
            print(f"  <select> name={name!r} id={el_id!r}")

        buttons = page.locator("button, input[type='submit'], input[type='button']")
        bcount = await buttons.count()
        print(f"\nReal buttons: {bcount}")
        for i in range(bcount):
            el = buttons.nth(i)
            try:
                text = (await el.inner_text()) or (await el.get_attribute("value")) or ""
                if text.strip():
                    print(f"  <button/input> text={text.strip()!r}")
            except Exception:
                pass

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
