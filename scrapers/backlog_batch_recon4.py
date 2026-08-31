#!/usr/bin/env python3
"""
PlanFind — backlog batch recon 4 (2026-08-31).

Resolves 3 remaining ambiguities with real UI interaction rather than
shortcuts that didn't work:

  - South Derbyshire: round 3 found the URL-query-param shortcut
    didn't filter anything. Direct HTML inspection then found why —
    the afterDate/beforeDate <input type="date"> fields are
    disabled="" by default, and only become enabled via a real
    Livewire wire:model.live reactive update once dateType is
    genuinely selected (value "1" = Validation Date). This selects
    dateType properly, waits for the Livewire round-trip, then fills
    the now-enabled date fields and checks whether the total actually
    drops below the base 32,240.

  - Rotherham: round 3's blind submit-click left the results genuinely
    empty (RecCount stayed unpopulated). Direct HTML inspection found
    the real cause is likely the required sort-order selects
    (wListSort1/2/3) all defaulting to blank — this explicitly selects
    a real value ("Received Date") for wListSort1 before submitting.

  - Fylde: round 3 only confirmed the disclaimer-accept flow and
    enumerated the real Advanced form fields — never actually
    submitted a real search. This fills DateReceivedFrom/To and
    submits, capturing the real /Search/Results structure. (Redcar &
    Cleveland shares near-identical field names — whatever's learned
    here likely transfers.)
"""
import asyncio
import re
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


async def save_evidence(page, slug: str):
    html = await page.content()
    out_html = f"/tmp/backlog4_recon_{slug}.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    out_png = f"/tmp/backlog4_recon_{slug}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
    except Exception:
        pass
    print(f"  Saved: {out_html}, {out_png}")


# ---------------------------------------------------------------------
# 1. South Derbyshire — real Livewire dateType selection + date fill
# ---------------------------------------------------------------------
async def recon_south_derbyshire(browser):
    print(f"\n{'=' * 70}")
    print("RECON: South Derbyshire — real Livewire date-filter interaction")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://planning.southderbyshire.gov.uk/",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        html_before = await page.content()
        total_before = re.search(r'"total":(\d+)', html_before)
        print(f"  Real total BEFORE any interaction: {total_before.group(1) if total_before else 'not found'}")

        # Select "Validation Date" (value=1) — this fires wire:model.live,
        # triggering a real Livewire AJAX round-trip that enables the
        # date inputs
        date_type_select = page.locator("select[wire\\:model\\.live='dateType']")
        await date_type_select.select_option("1", timeout=10_000)
        await asyncio.sleep(2)  # let the Livewire round-trip complete

        after_date = page.locator("input[wire\\:model\\.live='afterDate']")
        before_date = page.locator("input[wire\\:model\\.live='beforeDate']")
        is_disabled = await after_date.is_disabled()
        print(f"  afterDate field disabled after selecting dateType: {is_disabled}")

        if not is_disabled:
            await after_date.fill("2026-08-01", timeout=5_000)
            await before_date.fill("2026-08-30", timeout=5_000)
            await asyncio.sleep(2)  # let the Livewire round-trip complete

        html_after = await page.content()
        import html as htmlmod
        effects_matches = re.findall(r'wire:effects="([^"]*)"', html_after)
        real_total = None
        for raw in effects_matches:
            unescaped = htmlmod.unescape(raw)
            if '"dispatches"' in unescaped and '"data"' in unescaped:
                total_match = re.search(r'"total":(\d+)', unescaped)
                if total_match:
                    real_total = total_match.group(1)
                break
        print(f"  Real total AFTER interaction: {real_total or 'not found'}")
        print("  (Base was 32,240 — if the number above is meaningfully "
              "smaller, real UI interaction successfully filters it.)")

        await save_evidence(page, "south_derbyshire_real_interaction")

    except Exception as e:
        print(f"  ⚠ Interaction error: {type(e).__name__}: {e!r}")

    await context.close()


# ---------------------------------------------------------------------
# 2. Rotherham — select a real sort value before submitting
# ---------------------------------------------------------------------
async def recon_rotherham(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Rotherham — real sort-order selection before submit")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://planning.rotherham.gov.uk/weeklylistapp.asp",
                         wait_until="domcontentloaded", timeout=45_000)

        # REAL FIX (2026-08-31) — round4 recon selected a sort value and
        # clicked "Update" directly, but got back the exact same blank
        # landing state (RecCount=0, sort selects reset). This looks
        # like a two-step wizard: the week selection ("Go") likely needs
        # to be submitted first to register it into the server's
        # session state, before "Update" (sort selection) does anything
        # real. Clicking "Go" first (even with the default "Most
        # Recent" already selected) before touching sort order.
        go_button = page.locator("input[type='submit']").first
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await go_button.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        await page.select_option("#wListSort1", label="Received Date", timeout=10_000)

        submit_buttons = page.locator("input[type='submit']")
        count = await submit_buttons.count()
        print(f"  Found {count} submit buttons — clicking 'Update' (the one after sort selects)")

        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            await submit_buttons.nth(count - 1).click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass

        html = await page.content()
        rec_count_match = re.search(r'id="RecCount"[^>]*value="(\d+)"', html)
        print(f"  Real RecCount after submit: {rec_count_match.group(1) if rec_count_match else 'not found'}")

        body_text = (await page.locator("body").inner_text())[:2000]
        print(f"\n  Real visible body text (first 2000 chars): {body_text!r}")

        await save_evidence(page, "rotherham_real_sort_submit")

    except Exception as e:
        print(f"  ⚠ Interaction error: {type(e).__name__}: {e!r}")

    await context.close()


# ---------------------------------------------------------------------
# 3. Fylde — real date-range search submission
# ---------------------------------------------------------------------
async def recon_fylde(browser):
    print(f"\n{'=' * 70}")
    print("RECON: Fylde — real date-range search submission")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        await page.goto("https://pa.fylde.gov.uk/Search/Advanced",
                         wait_until="domcontentloaded", timeout=45_000)

        if "Disclaimer" in page.url:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                await page.click("button:has-text('Agree'), input[value='Agree']")

        if "Search/Advanced" not in page.url:
            await page.goto("https://pa.fylde.gov.uk/Search/Advanced",
                             wait_until="domcontentloaded", timeout=45_000)

        await page.fill("#DateReceivedFrom", "01/08/2026", timeout=5_000)
        await page.fill("#DateReceivedTo", "30/08/2026", timeout=5_000)

        # REAL FIX (2026-08-31) — round4 recon's input[type='submit']
        # selector timed out finding ANY match at all, unlike Redcar &
        # Cleveland (same platform family, but apparently a styled
        # <button> here rather than a plain <input type=submit>).
        submit = page.locator(
            "button:has-text('Search'), input[type='submit'], "
            "button[type='submit']"
        ).last
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
            await submit.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        print(f"  Final URL: {page.url}")
        body_text = (await page.locator("body").inner_text())[:2000]
        print(f"  Real visible body text (first 2000 chars): {body_text!r}")

        tables = page.locator("table")
        print(f"  {await tables.count()} <table> element(s) found on results page")

        await save_evidence(page, "fylde_real_results")

    except Exception as e:
        print(f"  ⚠ Interaction error: {type(e).__name__}: {e!r}")

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Backlog batch recon 4 "
          f"— real UI interaction for South Derbyshire, Rotherham, Fylde\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        await recon_south_derbyshire(browser)
        await recon_rotherham(browser)
        await recon_fylde(browser)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
