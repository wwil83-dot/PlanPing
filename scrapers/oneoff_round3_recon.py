#!/usr/bin/env python3
"""
PlanFind — Ipswich / Central Bedfordshire round 3 recon (2026-08-30).

Round 1 (oneoff_batch_recon.py, 27 Aug) only captured each council's
*landing* page. Both turned out to be one click short of the real search
form:

  - Ipswich: landing page (searchselect.asp) is a reference-number quick
    search only. The real "Planning Application Information" advanced
    search lives behind a separate page, reached via a plain JS
    window.location redirect (no form submission needed):
        https://ppc.ipswich.gov.uk/appnsearch.asp
    This recon navigates there directly and dumps the real form fields.

  - Central Bedfordshire (AcolNet): landing page's "Weekly list search"
    requires picking one of 87 individual parishes AND one specific
    week from a dropdown — no "all parishes" option, impractical as a
    primary route. However "More search options" links to a URL using a
    stable RIPNAME parameter (not the session-bound RIPSESSION token the
    other on-page forms use):
        https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/acolnetcgi.gov?ACTION=UNWRAP&RIPNAME=Root.pgesearch
    This recon navigates there directly to check whether it's a genuine
    bookmarkable/scriptable district-wide date-range search.
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
    ("Ipswich Borough Council — advanced search",
     "https://ppc.ipswich.gov.uk/appnsearch.asp"),
    ("Central Bedfordshire Council — more search options",
     "https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/acolnetcgi.gov?ACTION=UNWRAP&RIPNAME=Root.pgesearch"),
]


def slug(name: str) -> str:
    return (
        name.lower()
        .replace(" — ", "_")
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
    )


async def dump_form_fields(page):
    print(f"\n  Real form fields found on this page:")
    try:
        inputs = page.locator("input")
        count = await inputs.count()
        for i in range(min(count, 40)):
            el = inputs.nth(i)
            try:
                itype = await el.get_attribute("type") or ""
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                if itype.lower() not in ("hidden",):
                    print(f"    <input> type={itype!r} name={name!r} id={el_id!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ input dump error: {e}")

    try:
        selects = page.locator("select")
        count = await selects.count()
        for i in range(min(count, 15)):
            el = selects.nth(i)
            try:
                name = await el.get_attribute("name") or ""
                el_id = await el.get_attribute("id") or ""
                opts = await el.locator("option").all_inner_texts()
                print(f"    <select> name={name!r} id={el_id!r} options(first 10)={opts[:10]!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ select dump error: {e}")

    try:
        buttons = page.locator("button, input[type='submit'], input[type='button'], input[type='image'], a.button")
        count = await buttons.count()
        for i in range(min(count, 20)):
            el = buttons.nth(i)
            try:
                text = (await el.inner_text()) or (await el.get_attribute("value")) or (await el.get_attribute("alt")) or ""
                if text.strip():
                    print(f"    <button/input> text={text.strip()!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ button dump error: {e}")

    try:
        forms = page.locator("form")
        count = await forms.count()
        for i in range(min(count, 10)):
            el = forms.nth(i)
            try:
                action = await el.get_attribute("action") or ""
                method = await el.get_attribute("method") or ""
                print(f"    <form> action={action!r} method={method!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠ form dump error: {e}")


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
        print(f"  ⚠ Navigation error (real timeout/block signature): {e}")
        await context.close()
        return

    print(f"  Real HTTP status: {response.status if response else None}")
    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/oneoff_r3_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/oneoff_r3_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_form_fields(page)

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1500]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1500 chars): {body_text!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Round 3 recon "
          f"— Ipswich advanced search / Central Beds more-search-options\n")

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
