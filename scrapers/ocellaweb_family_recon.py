#!/usr/bin/env python3
"""
PlanFind — OcellaWeb platform family recon (2026-08-25).

Real, confirmed evidence: Great Yarmouth and South Holland share the
exact same real URL pattern (planning.{council}.gov.uk/OcellaWeb/
planningSearch), confirmed directly from earlier council-search
research. Havering and Hillingdon are ALSO confirmed to use this same
real OcellaWeb/Northgate system (found directly in idox_councils.py's
own disabled-entry comments while investigating this platform), but
only their base domain is confirmed — the exact full path is being
tested directly here rather than assumed to match the other two.

Both Havering and Hillingdon are on the real, confirmed "missing 13"
London boroughs list from an earlier coverage audit this project —
genuinely valuable if this platform works cleanly for all 4.
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

# Real, confirmed URLs for the first 2; best-guess full path for the
# other 2 (base domain confirmed, /planningSearch suffix assumed to
# match the confirmed pattern — being tested directly here, not
# trusted blindly)
TARGETS = [
    ("Great Yarmouth Borough Council",
     "https://planning.great-yarmouth.gov.uk/OcellaWeb/planningSearch"),
    ("South Holland District Council",
     "https://planning.sholland.gov.uk/OcellaWeb/planningSearch"),
    ("London Borough of Havering",
     "https://development.havering.gov.uk/OcellaWeb/planningSearch"),
    ("London Borough of Hillingdon",
     "https://planning.hillingdon.gov.uk/OcellaWeb/planningSearch"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


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
                placeholder = await el.get_attribute("placeholder") or ""
                if itype.lower() not in ("hidden",):
                    print(f"    <input> type={itype!r} name={name!r} id={el_id!r} placeholder={placeholder!r}")
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
                print(f"    <select> name={name!r} id={el_id!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ select dump error: {e}")

    try:
        buttons = page.locator("button, input[type='submit'], input[type='button']")
        count = await buttons.count()
        for i in range(min(count, 15)):
            el = buttons.nth(i)
            try:
                text = (await el.inner_text()) or (await el.get_attribute("value")) or ""
                el_id = await el.get_attribute("id") or ""
                if text.strip():
                    print(f"    <button/input> text={text.strip()!r} id={el_id!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ button dump error: {e}")


async def recon_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {name}")
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

    print(f"  Real HTTP status: {response.status if response else None}")
    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/ocellaweb_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/ocellaweb_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_form_fields(page)

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1000]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1000 chars): {body_text!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] OcellaWeb platform family recon "
          f"— {len(TARGETS)} candidate councils\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for name, url in TARGETS:
            await recon_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
