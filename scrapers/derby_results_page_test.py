#!/usr/bin/env python3
"""
PlanFind — Derby results-page direct test (2026-08-19).

REAL GAP this fills: every diagnostic run so far only ever tested
search.do?action=monthlyList — the FORM page. Never tested
monthlyListResults.do?action=firstPage — the actual RESULTS page —
directly, despite the person running this confirming they can view
real, current results at that exact URL in their own real browser
right now. Worth checking directly whether this specific endpoint is
blocked identically to the form page, or behaves differently, rather
than assuming they're the same.

Two real, different things this checks:
  1. Does a plain GET (no prior form visit, no session, no POST at all)
     to the results URL return real content from THIS runner — the
     same one already confirmed completely blocked (zero network
     activity, 120s timeout) for the form page?
  2. Does it work WITH the real "week" parameter appended (matching the
     real field name confirmed directly from Derby's own form HTML),
     to test whether the results can be controlled to a specific date
     range rather than just showing whatever's currently default —
     the real thing that would decide whether this is a genuinely
     useful scraping route or just a one-off lucky view.
"""
import asyncio
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors"]
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

LONG_TIMEOUT_MS = 60_000  # shorter than the 120s used before — if this
                           # works, it should work fast; if it's blocked
                           # the same way, 60s is already enough evidence

# Two real variants — plain URL as the person found it, and with the
# real "week" field appended (confirmed real field name from Derby's
# own form HTML, not guessed).
TARGETS = [
    ("Derby — results page, no params",
     "https://eplanning.derby.gov.uk/online-applications/monthlyListResults.do?action=firstPage"),
    ("Derby — results page, real week param appended",
     "https://eplanning.derby.gov.uk/online-applications/monthlyListResults.do"
     "?action=firstPage&week=03%20Aug%202026&dateType=DC_Validated&searchType=Application"),
]

network_log: list[dict] = []


async def _on_response(response):
    network_log.append({"url": response.url, "status": response.status})


async def test_one(browser, label: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"{label}")
    print(f"URL: {url}")
    print("=" * 70)

    network_log.clear()
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    page.on("response", lambda r: asyncio.create_task(_on_response(r)))

    start = datetime.now(timezone.utc)
    timed_out = False
    error = None
    real_response = None

    try:
        real_response = await page.goto(url, wait_until="domcontentloaded", timeout=LONG_TIMEOUT_MS)
    except PlaywrightTimeout:
        timed_out = True
    except Exception as e:
        error = str(e)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print(f"Elapsed: {elapsed:.1f}s")

    if timed_out:
        print(f"RESULT: Timed out at {LONG_TIMEOUT_MS/1000:.0f}s — blocked, same as the form page")
    elif error:
        print(f"RESULT: Navigation error: {error}")
    else:
        status = real_response.status if real_response else None
        print(f"RESULT: Real response — HTTP {status}, after {elapsed:.1f}s")
        title = await page.title()
        print(f"Real page title: {title!r}")
        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:2000]
        except Exception:
            pass
        print(f"Visible body text (first 2000 chars): {body_text!r}")

    print(f"\nReal network activity captured ({len(network_log)} entries):")
    if not network_log:
        print("  NONE — same signature as the confirmed block")
    else:
        for entry in network_log[:10]:
            print(f"  {entry}")

    try:
        out_png = f"/tmp/derby_results_test_{label.replace(' ', '_').replace('—', '')[:40]}.png"
        await page.screenshot(path=out_png, full_page=True)
        print(f"Saved screenshot: {out_png}")
    except Exception:
        pass

    await context.close()


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Derby results-page direct test\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for label, url in TARGETS:
            await test_one(browser, label, url)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("If either of these got real content where the form page always")
    print("times out, that's a genuinely different, better route in — worth")
    print("checking the saved screenshots directly either way.")


if __name__ == "__main__":
    asyncio.run(main())
