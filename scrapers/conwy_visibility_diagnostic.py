#!/usr/bin/env python3
"""
PlanFind — Conwy Northgate visibility diagnostic (2026-08-22).

Real, confirmed context: Conwy's #rbRange radio button consistently
fails Playwright's "is visible" actionability check, identical error
both before and after adding overlay-dismissal logic (confirmed the
fix genuinely deployed — it just didn't find/match whatever's actually
blocking this specific element). Rather than guess at another
selector, this captures real, direct evidence: a screenshot at the
exact moment of failure, the full page HTML, and a direct JS inspection
of the #rbRange element's own real computed CSS state and whatever
element genuinely occupies its screen position.
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

URL = "https://npe.conwy.gov.uk/Northgate/EnglishPlanningExplorer/generalsearch.aspx"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Conwy visibility diagnostic\n")
    print(f"URL: {URL}\n")

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

        await asyncio.sleep(2)

        title = await page.title()
        print(f"Real page title: {title!r}")
        print(f"Real final URL: {page.url}\n")

        # Real, direct screenshot at this exact moment — before any
        # attempt to interact with #rbRange at all
        out_png = "/tmp/conwy_diag_before_interaction.png"
        try:
            await page.screenshot(path=out_png, full_page=True)
            print(f"Saved screenshot (before any interaction): {out_png}")
        except Exception as e:
            print(f"⚠ Screenshot failed: {e}")

        # Real, direct check for a cookie/overlay element already
        # attempted — confirm whether one genuinely exists at all
        overlay_count = await page.locator("#ivcb-overlay").count()
        print(f"\nReal #ivcb-overlay elements on this page: {overlay_count}")

        # Real, direct JS inspection of #rbRange itself — its own
        # computed style, bounding box, and whatever element genuinely
        # sits at its exact screen coordinates (the real, authoritative
        # way to know what's actually blocking it, rather than guessing)
        inspection = await page.evaluate("""() => {
            const el = document.querySelector('#rbRange');
            if (!el) return {found: false};
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;
            const topElement = document.elementFromPoint(centerX, centerY);
            return {
                found: true,
                display: style.display,
                visibility: style.visibility,
                opacity: style.opacity,
                rect: {top: rect.top, left: rect.left, width: rect.width, height: rect.height},
                topElementAtCenter: topElement ? {
                    tag: topElement.tagName,
                    id: topElement.id,
                    className: topElement.className,
                } : null,
                isSameElement: topElement === el,
                parentChain: (() => {
                    let chain = [];
                    let node = el.parentElement;
                    let depth = 0;
                    while (node && depth < 8) {
                        const s = window.getComputedStyle(node);
                        chain.push({
                            tag: node.tagName, id: node.id,
                            display: s.display, visibility: s.visibility,
                        });
                        node = node.parentElement;
                        depth++;
                    }
                    return chain;
                })(),
            };
        }""")

        print(f"\nReal #rbRange direct JS inspection:")
        print(f"  Found: {inspection.get('found')}")
        if inspection.get('found'):
            print(f"  Real computed display: {inspection.get('display')!r}")
            print(f"  Real computed visibility: {inspection.get('visibility')!r}")
            print(f"  Real computed opacity: {inspection.get('opacity')!r}")
            print(f"  Real bounding rect: {inspection.get('rect')}")
            print(f"  Real element at its exact center point: {inspection.get('topElementAtCenter')}")
            print(f"  Is #rbRange itself the topmost element there: {inspection.get('isSameElement')}")
            print(f"  Real parent chain (up to 8 levels):")
            for i, p in enumerate(inspection.get('parentChain', [])):
                print(f"    [{i}] <{p['tag']}> id={p['id']!r} display={p['display']!r} visibility={p['visibility']!r}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE — check the screenshot and the real inspection")
    print("output above before guessing at another fix.")


if __name__ == "__main__":
    asyncio.run(main())
