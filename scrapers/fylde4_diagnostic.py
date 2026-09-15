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


async def inspect_hidden_field_safely(page, label: str) -> None:
    """REAL FIX (round 3) — round 2's 8 guessed toggle-click attempts
    likely caused a real, destructive side effect: the follow-up
    parent-chain check timed out entirely waiting for the element,
    consistent with the page having reloaded/reset after one of those
    blind clicks (a plausible candidate: "button:has-text('Advanced')"
    may have matched a real form-submitting button, not a safe
    collapsible toggle). Rather than keep guessing at clicks that risk
    the same problem again, this only OBSERVES the real raw HTML around
    the field — zero interaction, zero risk of triggering a reload."""
    try:
        html = await page.content()
    except Exception as e:
        print(f"    [{label}] Could not get page content: {type(e).__name__}: {e}")
        return

    idx = html.find('id="DateReceivedFrom"')
    if idx == -1:
        idx = html.find("id='DateReceivedFrom'")
    if idx == -1:
        print(f"    [{label}] Could not find DateReceivedFrom in raw HTML at all")
        return

    # Real surrounding markup — enough context to see any wrapping
    # element's class/style that might explain why it's hidden.
    start = max(0, idx - 800)
    end = min(len(html), idx + 200)
    snippet = html[start:end]
    print(f"    [{label}] Real raw HTML surrounding #DateReceivedFrom "
          f"(800 chars before, 200 after):")
    print(f"      {snippet!r}")


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

    field = page.locator("#DateReceivedFrom")
    if await field.count() > 0:
        is_visible = await field.first.is_visible()
        print(f"    [{name}] #DateReceivedFrom real visibility: {is_visible}")
        if not is_visible:
            await inspect_hidden_field_safely(page, name)
    else:
        print(f"    [{name}] #DateReceivedFrom not found in DOM at all")

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
            # REAL FIX (round 3) — confirmed via the actual error even
            # with force=True: "Element is outside of the viewport".
            # force=True bypasses the visibility check but Playwright's
            # mouse-based click simulation still requires real
            # coordinates within the viewport, which this element
            # genuinely doesn't have. Setting the state directly via
            # JS and dispatching a real 'change' event is the correct
            # technique for a genuinely off-screen-but-functional
            # native control — bypasses mouse simulation entirely.
            try:
                await planning_checkbox.first.evaluate(
                    "el => { el.checked = true; el.dispatchEvent(new Event('change', {bubbles: true})); }"
                )
                is_now_checked = await planning_checkbox.first.is_checked()
                print(f"    [{name}] SearchPlanning checkbox set via direct JS — "
                      f"real checked state now: {is_now_checked}")
            except Exception as e:
                print(f"    [{name}] REAL ERROR setting checkbox via JS: "
                      f"{type(e).__name__}: {e}")

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

    # Now that the checkbox blocker is fixed, actually attempt a real
    # submission to confirm the fix end-to-end, rather than just
    # re-dumping the same button info again.
    try:
        submit = scope.locator(
            "button:has-text('Search'):visible, input[type='submit'][value*='Search' i]:visible"
        )
        submit_count = await submit.count()
        if submit_count > 0:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
                await submit.first.click(timeout=5_000)
            print(f"    [{name}] REAL SUCCESS — submitted, post-submit URL: {page.url}")
        else:
            print(f"    [{name}] Still no visible Search submit found for a real attempt")
    except Exception as e:
        print(f"    [{name}] REAL ERROR during submit attempt: {type(e).__name__}: {e}")

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

    # REAL FIX (round 3) — confirmed via the actual error: the field is
    # <input type="date">, a native HTML5 date picker, which requires
    # ISO format (YYYY-MM-DD) as its underlying value regardless of
    # how the page visually displays dates — DD/MM/YYYY produced a
    # "Malformed value" error. Genuinely different from every other
    # confirmed platform in this cluster, which all use plain text
    # inputs accepting DD/MM/YYYY.
    start_iso = start.isoformat()
    today_iso = today.isoformat()

    try:
        await page.fill("#DateReceivedFrom", start_iso, timeout=5_000)
        print(f"    [{name}] DateReceivedFrom filled OK (ISO format: {start_iso})")
    except Exception as e:
        print(f"    [{name}] REAL ERROR filling DateReceivedFrom: "
              f"{type(e).__name__}: {e}")

    try:
        await page.fill("#DateReceivedTo", today_iso, timeout=5_000)
        print(f"    [{name}] DateReceivedTo filled OK (ISO format: {today_iso})")
    except Exception as e:
        print(f"    [{name}] REAL ERROR filling DateReceivedTo: "
              f"{type(e).__name__}: {e}")

    planning_checkbox = page.locator("input[name='SearchPlanning'][type='checkbox']")
    try:
        if await planning_checkbox.count() > 0 and not await planning_checkbox.first.is_checked():
            await planning_checkbox.first.evaluate(
                "el => { el.checked = true; el.dispatchEvent(new Event('change', {bubbles: true})); }"
            )
            is_now_checked = await planning_checkbox.first.is_checked()
            print(f"    [{name}] SearchPlanning checkbox set via direct JS — "
                  f"real checked state now: {is_now_checked}")
    except Exception as e:
        print(f"    [{name}] REAL ERROR setting checkbox via JS: {type(e).__name__}: {e}")

    form_loc = page.locator("form").filter(has=page.locator("#DateReceivedFrom"))
    form_count = await form_loc.count()
    scope = form_loc if form_count > 0 else page

    try:
        # REAL FIX (round 3) — confirmed via the actual dump: the real
        # submit control is <input type="submit" value="Search">, not
        # a <button> tag at all. button:has-text() can never match an
        # <input>, which has no inner text content to search — only a
        # value attribute. Broadened to check both real shapes.
        submit = scope.locator(
            "button:has-text('Search'):visible, input[type='submit'][value*='Search' i]:visible"
        )
        submit_count = await submit.count()
        print(f"    [{name}] Real visible Search submit count in scope: {submit_count}")
        if submit_count > 0:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
                await submit.first.click(timeout=5_000)
            print(f"    [{name}] REAL SUCCESS — submitted, post-submit URL: {page.url}")
        else:
            print(f"    [{name}] No visible Search button/input found — dumping real buttons:")
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

        # REAL BUG FIX (round 2) — the actual run confirmed this
        # matters: West Northants' unhandled checkbox timeout crashed
        # the ENTIRE script (exit code 1), meaning Welwyn Hatfield
        # never even ran at all. Isolating each candidate individually,
        # matching the same discipline used throughout this project's
        # other multi-council recon scripts (e.g. fylde_cluster_recon.py),
        # so one candidate's failure never blocks the others.
        try:
            await diagnose_vowh_soxon(browser, *CANDIDATES[0])
        except Exception as e:
            print(f"\n⚠ Unexpected error diagnosing {CANDIDATES[0][0]}: {type(e).__name__}: {e}")

        try:
            await diagnose_vowh_soxon(browser, *CANDIDATES[1])
        except Exception as e:
            print(f"\n⚠ Unexpected error diagnosing {CANDIDATES[1][0]}: {type(e).__name__}: {e}")

        try:
            await diagnose_wnorthants(browser, *CANDIDATES[2])
        except Exception as e:
            print(f"\n⚠ Unexpected error diagnosing {CANDIDATES[2][0]}: {type(e).__name__}: {e}")

        try:
            await diagnose_welwyn(browser, *CANDIDATES[3])
        except Exception as e:
            print(f"\n⚠ Unexpected error diagnosing {CANDIDATES[3][0]}: {type(e).__name__}: {e}")

        await browser.close()

    print("\nDiagnostic complete. Check the real findings above for each council.")


if __name__ == "__main__":
    asyncio.run(main())
