#!/usr/bin/env python3
"""
PlanFind — Westmorland and Furness recon (2026-08-22).

TWO GENUINELY SEPARATE REAL SYSTEMS, confirmed via real web research
(not guessed) — this council is a 2023 merger of three former areas
(Eden, South Lakeland, Barrow-in-Furness) that never got consolidated
onto shared planning software:

1. Eden and South Lakeland areas — own real register at
   planningregister.westmorlandandfurness.gov.uk. Real weekly-list
   PDFs confirmed a rich structure: reference, address, proposal,
   Easting/Northing, applicant, application type, decision, decision
   date, parish — plus a real per-application detail link pattern.
   Covers applications back to 1988.

2. Barrow area — a completely separate system, "Barrow Planning Hub",
   built on Oracle APEX (confirmed via the distinctive
   webapps.barrowbc.gov.uk/webapps/f?p=BARROWPLANNINGHUB:... URL
   signature — a genuinely different platform type from anything else
   in this project). Real evidence: only shows the last 7 years.

Real recon goal: capture actual HTML/screenshots for both systems'
real search interfaces before writing any parsing logic — same
discipline as every other platform this session.
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


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("/", "_")


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
                opt_count = await el.locator("option").count()
                print(f"    <select> name={name!r} id={el_id!r} ({opt_count} options)")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ select dump error: {e}")

    try:
        buttons = page.locator("button, input[type='submit'], input[type='button']")
        count = await buttons.count()
        for i in range(min(count, 20)):
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

    print(f"\n  Real visible links on this page:")
    try:
        links = page.locator("a")
        count = await links.count()
        seen = set()
        for i in range(min(count, 40)):
            el = links.nth(i)
            try:
                text = (await el.inner_text()).strip()
                href = await el.get_attribute("href") or ""
                if text and text not in seen and len(text) < 80:
                    seen.add(text)
                    print(f"    {text!r} -> {href[:100]}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ link dump error: {e}")


async def recon_page(browser, name: str, url: str):
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

    print(f"  HTTP status: {response.status if response else None}")
    await asyncio.sleep(1)

    # Real, generic cookie-banner dismissal attempt, same discipline as
    # every other recon this session
    for label in ["Accept", "Accept all", "I agree", "Allow all cookies"]:
        try:
            btn = page.get_by_role("button", name=label, exact=False)
            if await btn.count() > 0 and await btn.first.is_visible(timeout=1500):
                await btn.first.click()
                await asyncio.sleep(1)
                print(f"  Dismissed a cookie banner via {label!r}")
                break
        except Exception:
            continue

    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/wandf_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/wandf_recon_{slug(name)}.png"
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] Westmorland and Furness recon "
          f"— 2 genuinely separate systems\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        # System 1 — Eden and South Lakeland register
        await recon_page(
            browser, "Eden South Lakeland Quick Search",
            "https://planningregister.westmorlandandfurness.gov.uk/",
        )

        # System 2 — Barrow Planning Hub (Oracle APEX)
        await recon_page(
            browser, "Barrow Planning Hub",
            "https://webapps.barrowbc.gov.uk/webapps/f?p=BARROWPLANNINGHUB:WEEKLYLIST:10007760192139::NO:::",
        )

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)
    print("Download the workflow artifact and read the saved HTML/screenshots")
    print("directly before writing any scraper code — same discipline as every")
    print("other platform this session. In particular: does the Eden/South")
    print("Lakeland page have a real, clickable 'Advanced Search' link visible")
    print("from the Quick Search landing page, and what does Barrow's real")
    print("Oracle APEX weekly-list page actually look like structurally?")


if __name__ == "__main__":
    asyncio.run(main())
