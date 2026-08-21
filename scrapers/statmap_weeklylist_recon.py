#!/usr/bin/env python3
"""
PlanFind — statmap.co.uk/horizoNext, round 2: real 'Weekly Lists' tab
recon (2026-08-21).

Round 1 loaded the default Property Search tab (address/postcode/UPRN
lookup — not useful for us) and only confirmed a real "Weekly Lists"
button exists, matching the user's own original research note
("BESPOKE with date range searches and weekly searches"). This round
clicks through to that real tab specifically and dumps its actual real
form structure — nothing assumed from round 1's incomplete picture.
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

TARGETS = [
    ("West Lindsey District Council",
     "https://westlindsey-publicportal.statmap.co.uk/horizoNext/publicportal"),
    ("East Staffordshire Borough Council",
     "https://eaststaffs-publicportal.statmap.co.uk/horizoNext/publicportal"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def dump_full_page_structure(page):
    print(f"\n  Real form fields on THIS tab:")
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
                options = await el.locator("option").all_text_contents()
                print(f"    <select> name={name!r} id={el_id!r} options={options[:15]}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ select dump error: {e}")

    # Real, distinctive react/mui-style date pickers often use
    # role="textbox" or a specific aria-label rather than a plain
    # <input type=date> — checking broadly rather than assuming one
    # specific real pattern
    try:
        aria_els = page.locator("[aria-label]")
        count = await aria_els.count()
        for i in range(min(count, 30)):
            el = aria_els.nth(i)
            try:
                label = await el.get_attribute("aria-label") or ""
                tag = await el.evaluate("el => el.tagName")
                if label and "date" in label.lower():
                    print(f"    <{tag.lower()}> aria-label={label!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ aria-label dump error: {e}")

    try:
        buttons = page.locator("button")
        count = await buttons.count()
        for i in range(min(count, 20)):
            el = buttons.nth(i)
            try:
                text = (await el.inner_text()).strip()
                if text:
                    print(f"    <button> text={text!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ button dump error: {e}")


async def recon_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"WEEKLY LISTS RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        await context.close()
        return

    # Real cookie banner confirmed present in round 1 — dismiss it
    # before any interaction, same discipline as the Northgate recon.
    for label in ["Accept additional cookies", "Accept"]:
        try:
            btn = page.get_by_role("button", name=label, exact=False)
            if await btn.count() > 0 and await btn.first.is_visible(timeout=2000):
                await btn.first.click()
                await asyncio.sleep(1)
                print(f"  Dismissed cookie banner via {label!r}")
                break
        except Exception:
            continue

    # Real, direct click on the confirmed "Weekly Lists" button
    try:
        weekly_btn = page.get_by_role("button", name="Weekly Lists", exact=False)
        await weekly_btn.click(timeout=10_000)
        print(f"  Clicked 'Weekly Lists' tab")
    except Exception as e:
        print(f"  ⚠ Could not click 'Weekly Lists': {e}")
        await context.close()
        return

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(2)  # real, deliberate pause for any client-side
                             # panel transition to finish rendering

    title = await page.title()
    print(f"  Real page title after click: {title!r}")
    print(f"  Real URL after click: {page.url}")

    html = await page.content()
    out_html = f"/tmp/statmap_weeklylist_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/statmap_weeklylist_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_full_page_structure(page)

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1500]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1500 chars): {body_text!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] statmap Weekly Lists "
          f"round-2 recon — {len(TARGETS)} councils\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for name, url in TARGETS:
            await recon_one(browser, name, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)
    print("Download the workflow artifact and read the saved HTML/screenshots")
    print("directly — particularly whether real date fields appear, and what")
    print("their actual real field names/interaction pattern is, before writing")
    print("any scraper code.")


if __name__ == "__main__":
    asyncio.run(main())
