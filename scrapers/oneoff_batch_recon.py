#!/usr/bin/env python3
"""
PlanFind — one-off council batch recon (2026-08-27).

Real, confirmed current URLs for 8 genuinely uninvestigated councils
from the fresh council-search batch. Two real corrections found during
research before this recon was even built:
  - Wolverhampton: confirmed ALREADY fixed and active in idox_councils.py
    from an earlier session — the "possibly broken" roadmap flag was
    simply stale. Not included here, nothing to investigate.
  - Charnwood: the roadmap's "Assure" platform note was wrong/stale —
    real, current URL is a Northgate/PlanningExplorerAA path, the same
    real platform family already built and proven this session
    (Runnymede, Conwy, Tamworth). Included here to verify rather than
    assume identical behaviour, given how much real per-council
    variance has shown up even within confirmed shared platforms.
  - Medway: confirmed genuinely moved off Idox (old URL already dead
    and disabled) to Open Digital Planning — but that register is
    explicitly, officially described as an incomplete pilot ("only a
    limited set of applications are being published here"). Included
    at the user's request specifically to scope how partial it looks
    in practice, not assumed to be a clean primary source.
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
    ("Charnwood Borough Council",
     "https://portal.charnwood.gov.uk/Northgate/PlanningExplorerAA/GeneralSearch.aspx"),
    ("Walsall Metropolitan Borough Council",
     "https://planning.walsall.gov.uk/swift/apas/run/wphappcriteria.display"),
    ("Central Bedfordshire Council",
     "https://plantech.centralbedfordshire.gov.uk/PLANTECH/DCWebPages/AcolNetCGI.gov"),
    ("Ipswich Borough Council",
     "https://ppc.ipswich.gov.uk/searchselect.asp"),
    ("Stratford-on-Avon District Council",
     "https://apps.stratford.gov.uk/eplanningv2/Home/MonthlyList"),
    ("Herefordshire Council",
     "https://www.herefordshire.gov.uk/planning-and-building-control/planning-search"),
    ("Greater Cambridge Shared Planning Service",
     "https://applications.greatercambridgeplanning.org/online-applications/"),
    ("Medway Council (Open Digital Planning)",
     "https://planningregister.org/medway"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("(", "").replace(")", "")


async def dump_form_fields(page):
    print(f"\n  Real form fields found on this page:")
    try:
        inputs = page.locator("input")
        count = await inputs.count()
        for i in range(min(count, 25)):
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
        buttons = page.locator("button, input[type='submit'], input[type='button'], a.button")
        count = await buttons.count()
        for i in range(min(count, 15)):
            el = buttons.nth(i)
            try:
                text = (await el.inner_text()) or (await el.get_attribute("value")) or ""
                if text.strip():
                    print(f"    <button/input> text={text.strip()!r}")
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
        print(f"  ⚠ Navigation error (real timeout/block signature): {e}")
        await context.close()
        return

    print(f"  Real HTTP status: {response.status if response else None}")
    title = await page.title()
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {page.url}")

    html = await page.content()
    out_html = f"/tmp/oneoff_recon_{slug(name)}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/oneoff_recon_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")

    await dump_form_fields(page)

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:1200]
    except Exception:
        pass
    print(f"\n  Real visible body text (first 1200 chars): {body_text!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] One-off council batch recon "
          f"— {len(TARGETS)} candidates\n")

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
