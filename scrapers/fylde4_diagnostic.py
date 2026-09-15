#!/usr/bin/env python3
"""
PlanFind — Fylde-cluster remaining-4 diagnostic, round 2 (2026-09-13).

Real, specific follow-up to fylde_cluster_recon.py for the 4 candidates
that still couldn't submit a real search, despite all 4 being confirmed
to share Fylde's exact field names (DateReceivedFrom/DateReceivedTo
etc. all present in the DOM):

  - Vale of White Horse / South Oxfordshire: real evidence trail —
    the user's own ORIGINAL manual browsing notes said "there is
    another planning title which produces a drop down that has more
    date range searches" — meaning the real date fields may be present
    in the DOM but hidden inside a collapsed section, not yet visible/
    interactable, which would explain a fill() timeout despite the
    field genuinely existing. Tests this directly: checks real
    visibility, and if hidden, looks for a plausible expandable
    header/toggle to click before retrying.

  - West Northants: reached the SearchPlanning-checkbox step
    successfully but no submit button could be found within the form
    scope. Captures the real form's full button/input markup directly
    rather than guessing at another selector blind.

  - Welwyn Hatfield: failed with a bare "Error" in the prior round —
    no real detail was captured. This prints the full real exception
    type AND message this time, not just the type name.
"""
import asyncio
from datetime import date, timedelta

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

CANDIDATES = [
    ("Vale of White Horse District Council", "https://valeofwhitehorse.planning-register.co.uk/Search/Advanced"),
    ("South Oxfordshire District Council", "https://southoxfordshire.planning-register.co.uk/Search/Advanced"),
    ("West Northamptonshire Council", "https://wnc.planning-register.co.uk/Search/Advanced"),
    ("Welwyn Hatfield Borough Council", "https://planning.welhat.gov.uk/Search/Advanced"),
]


def _safe_name(name: str) -> str:
    return name.lower().replace(" ", "_").replace(",", "")


async def click_through_disclaimer(page, label: str) -> bool:
    if "Disclaimer" not in page.url:
        return True
    for selector in [
        "input[value*='Accept' i]", "button:has-text('Accept')",
        "input[value='Agree']", "button:has-text('Agree')",
        "input[value*='Agree' i]", "button:has-text('Continue')",
    ]:
        try:
            loc = page.locator(selector)
            if await loc.count() > 0:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                    await loc.first.click(timeout=5_000)
                print(f"    [{label}] Disclaimer click succeeded via: {selector!r}")
                return True
        except Exception:
            continue
    print(f"    [{label}] Could not click through disclaimer")
    return False


async def try_reveal_hidden_date_field(page, label: str) -> bool:
    """Real test of the collapsible-section theory. Checks whether
    #DateReceivedFrom is genuinely visible; if not, looks for plausible
    expandable headers/toggles near it and tries clicking them, then
    re-checks visibility. Returns True if the field is (or becomes)
    visible."""
    field = page.locator("#DateReceivedFrom")
    if await field.count() == 0:
        print(f"    [{label}] #DateReceivedFrom not found in DOM at all")
        return False

    is_visible = await field.first.is_visible()
    print(f"    [{label}] #DateReceivedFrom real visibility BEFORE any interaction: {is_visible}")
    if is_visible:
        return True

    candidates = [
        "text=Planning", "legend:has-text('Planning')",
        "[aria-expanded='false']", "button:has-text('Advanced')",
        "a:has-text('Planning')", ".accordion-header", ".collapsible",
        "[data-toggle='collapse']",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count == 0:
                continue
            for i in range(min(count, 3)):
                try:
                    await loc.nth(i).click(timeout=3_000)
                    await asyncio.sleep(0.5)
                    now_visible = await field.first.is_visible()
                    if now_visible:
                        print(f"    [{label}] #DateReceivedFrom became VISIBLE "
                              f"after clicking {sel!r} (match {i})")
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    print(f"    [{label}] #DateReceivedFrom still not visible after trying "
          f"{len(candidates)} plausible expandable-section selectors")
    return False


async def diagnose_vowh_soxon(browser, name: str, url: str):
    print(f"\n{'=' * 60}\nDIAGNOSE: {name}\n{'=' * 60}")
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    await click_through_disclaimer(page, name)

    revealed = await try_reveal_hidden_date_field(page, name)

    if revealed:
        print(f"    [{name}] Attempting real fill now that field is visible...")
        try:
            today = date.today()
            start = today - timedelta(days=30)
            await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
            await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)
            print(f"    [{name}] REAL SUCCESS — fill worked after revealing the section")
        except Exception as e:
            print(f"    [{name}] Fill still failed even after reveal attempt: "
                  f"{type(e).__name__}: {e}")

    safe = _safe_name(name)
    await page.screenshot(path=f"/tmp/fylde4_{safe}.png", full_page=True)
    html = await page.content()
    with open(f"/tmp/fylde4_{safe}.html", "w", encoding="utf-8") as f:
        f.write(html)

    await context.close()


async def diagnose_wnorthants(browser, name: str, url: str):
    print(f"\n{'=' * 60}\nDIAGNOSE: {name}\n{'=' * 60}")
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    await click_through_disclaimer(page, name)

    today = date.today()
    start = today - timedelta(days=30)
    try:
        await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
        await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)
        print(f"    [{name}] Date fields filled successfully")
    except Exception as e:
        print(f"    [{name}] Date fill failed: {type(e).__name__}: {e}")

    planning_checkbox = page.locator("input[name='SearchPlanning'][type='checkbox']")
    if await planning_checkbox.count() > 0:
        if not await planning_checkbox.first.is_checked():
            await planning_checkbox.first.check(timeout=5_000)
            print(f"    [{name}] SearchPlanning checkbox ticked")

    form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
    form_count = await form_loc.count()
    print(f"    [{name}] Real enclosing <form> count: {form_count}")
    scope = form_loc if form_count > 0 else page

    print(f"    [{name}] Real button/submit-like elements within scope:")
    elements = await scope.locator(
        "button, input[type='submit'], input[type='button'], a.btn, a.button"
    ).all()
    for i, el in enumerate(elements[:20]):
        try:
            outer = await el.evaluate("el => el.outerHTML")
            visible = await el.is_visible()
            print(f"      [{i}] visible={visible} {outer[:200]!r}")
        except Exception:
            continue

    safe = _safe_name(name)
    await page.screenshot(path=f"/tmp/fylde4_{safe}.png", full_page=True)
    html = await page.content()
    with open(f"/tmp/fylde4_{safe}.html", "w", encoding="utf-8") as f:
        f.write(html)

    await context.close()


async def diagnose_welwyn(browser, name: str, url: str):
    print(f"\n{'=' * 60}\nDIAGNOSE: {name}\n{'=' * 60}")
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    today = date.today()
    start = today - timedelta(days=30)

    try:
        await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
        print(f"    [{name}] DateReceivedFrom filled OK")
    except Exception as e:
        print(f"    [{name}] REAL ERROR filling DateReceivedFrom: "
              f"{type(e).__name__}: {e}")

    try:
        await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)
        print(f"    [{name}] DateReceivedTo filled OK")
    except Exception as e:
        print(f"    [{name}] REAL ERROR filling DateReceivedTo: "
              f"{type(e).__name__}: {e}")

    planning_checkbox = page.locator("input[name='SearchPlanning'][type='checkbox']")
    try:
        if await planning_checkbox.count() > 0 and not await planning_checkbox.first.is_checked():
            await planning_checkbox.first.check(timeout=5_000)
            print(f"    [{name}] SearchPlanning checkbox ticked OK")
    except Exception as e:
        print(f"    [{name}] REAL ERROR ticking checkbox: {type(e).__name__}: {e}")

    form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
    form_count = await form_loc.count()
    scope = form_loc if form_count > 0 else page

    try:
        submit = scope.locator("button:has-text('Search'):visible")
        submit_count = await submit.count()
        print(f"    [{name}] Real visible 'Search' button count in scope: {submit_count}")
        if submit_count > 0:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
                await submit.first.click(timeout=5_000)
            print(f"    [{name}] REAL SUCCESS — submitted, post-submit URL: {page.url}")
        else:
            print(f"    [{name}] No visible Search button found — dumping real buttons:")
            elements = await scope.locator("button, input[type='submit']").all()
            for i, el in enumerate(elements[:15]):
                try:
                    outer = await el.evaluate("el => el.outerHTML")
                    print(f"      [{i}] {outer[:200]!r}")
                except Exception:
                    continue
    except Exception as e:
        print(f"    [{name}] REAL ERROR during submit attempt: {type(e).__name__}: {e}")

    safe = _safe_name(name)
    await page.screenshot(path=f"/tmp/fylde4_{safe}.png", full_page=True)
    html = await page.content()
    with open(f"/tmp/fylde4_{safe}.html", "w", encoding="utf-8") as f:
        f.write(html)

    await context.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await diagnose_vowh_soxon(browser, *CANDIDATES[0])
        await diagnose_vowh_soxon(browser, *CANDIDATES[1])
        await diagnose_wnorthants(browser, *CANDIDATES[2])
        await diagnose_welwyn(browser, *CANDIDATES[3])

        await browser.close()

    print("\nDiagnostic complete. Check the real findings above for each council.")


if __name__ == "__main__":
    asyncio.run(main())
