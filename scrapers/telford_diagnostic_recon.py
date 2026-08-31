#!/usr/bin/env python3
"""
PlanFind — Telford diagnostic recon (2026-08-31).

Two live scraper runs both landed back on the blank search form via
?aspxerrorpath=/planningsearch/default.aspx — classic ASP.NET
behaviour when a customErrors redirect catches an unhandled server
exception during postback. The final (redirected) page was captured
both times, but never the actual error response itself, so the real
cause is still unknown.

This attaches a response listener BEFORE submitting, so if the
postback itself returns a 500 (or any non-200) with real error detail
in the body before the customErrors redirect fires, we capture it
directly — plus tries an alternate date format in case DD/MM/YYYY
text-fill isn't what the server-side date parser expects.
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

SEARCH_URL = "https://secure.telford.gov.uk/planningsearch/"


async def run_attempt(date_from: str, date_to: str, label: str):
    print(f"\n{'=' * 70}")
    print(f"ATTEMPT: {label} (DCdatefrom={date_from!r}, DCdateto={date_to!r})")
    print("=" * 70)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        responses = []

        def on_response(response):
            responses.append((response.status, response.url))

        page.on("response", on_response)

        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
            await page.fill("#ctl00_ContentPlaceHolder1_DCdatefrom", date_from, timeout=5_000)
            await page.fill("#ctl00_ContentPlaceHolder1_DCdateto", date_to, timeout=5_000)

            responses.clear()  # only care about what happens from submit onward
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=45_000):
                await page.click("#ctl00_ContentPlaceHolder1_btnSearchPlanningDetails")
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass

            print(f"  Final URL: {page.url}")
            print(f"  Real responses seen during/after submit ({len(responses)}):")
            for status, url in responses:
                print(f"    {status} {url}")

            html = await page.content()
            if "aspxerrorpath" in page.url:
                print("  ⚠ Still hit the customErrors redirect.")
                # Look for any leaked error detail in the body just in case
                if "Exception" in html or "Stack Trace" in html or "Server Error" in html:
                    idx = html.find("Exception")
                    if idx == -1:
                        idx = html.find("Server Error")
                    print(f"  Possible leaked error detail: {html[max(0,idx-200):idx+500]!r}")
                else:
                    print("  No leaked exception detail in the final rendered HTML "
                          "(customErrors is fully hiding it).")
            else:
                print("  ✓ No aspxerrorpath — this attempt may have worked!")
                body_text = (await page.locator("body").inner_text())[:1500]
                print(f"  Real body text: {body_text!r}")

        except Exception as e:
            print(f"  ⚠ Exception during attempt: {type(e).__name__}: {e!r}")

        await context.close()
        await browser.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Telford diagnostic recon\n")

    # Attempt 1: same format the live scraper already tried (for a
    # direct, controlled comparison with response-level visibility)
    await run_attempt("01/08/2026", "30/08/2026", "DD/MM/YYYY (same as live scraper)")

    # Attempt 2: ISO format, in case the server-side parser expects it
    # despite the UK-style display
    await run_attempt("2026-08-01", "2026-08-30", "ISO YYYY-MM-DD")

    # Attempt 3: only fill DCdatefrom, leave DCdateto blank, in case a
    # date RANGE specifically is what's triggering the exception
    await run_attempt("01/08/2026", "", "DCdatefrom only, DCdateto blank")

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
