#!/usr/bin/env python3
"""
PlanFind — Priority 1 diagnostic: Sheffield, Bassetlaw, North East
Lincolnshire, Derby (2026-08-19).

Real evidence so far, gathered across two separate real production runs
plus a direct SQL check:
  - All 4 confirmed via SQL: coverage_source still 'pending',
    last_saved_at still null — never once succeeded despite being
    configured for weeks, running in the regular nightly batch rotation
    the whole time.
  - Today's real batch-1 log: 3 of the 4 (Sheffield, Bassetlaw, Derby —
    North East Lincolnshire wasn't in this particular batch) all failed
    with the IDENTICAL bare "⚠ Page load timeout" — no WAF page title,
    no 429, no results-container diagnostic, nothing. The navigation
    itself just never completed within the 45s timeout.
  - Real code check: a bare page-load timeout gets ZERO retries in
    idox_scraper.py, unlike a 429 (2 retries with jittered backoff) —
    TRY_FIRSTPAGE_FALLBACK_COUNCILS, the only retry-adjacent mechanism
    for timeouts, is currently an EMPTY set, so it doesn't apply to any
    council right now, these 4 included.

OPEN QUESTION this diagnostic exists to answer, with real evidence
rather than a guess: is this genuine network-level blocking (a
connection that never gets a response at all — matches "the WAF
silently drops the packet" behaviour, distinct from a WAF that quickly
serves a challenge/block PAGE, which is what timeouts on other councils
in this project have always turned out to be), or is it just that these
4 sites are consistently slow and 45s genuinely isn't enough, worsened
by running concurrently with other councils competing for the same
runner's network/CPU?

This script tests each of the 4 ALONE (no concurrency at all — rules
out "unlucky timing under load" as a factor), with a MUCH longer
timeout (120s vs the real scraper's 45s) and captures a screenshot plus
whatever partial network activity happened even on failure, so the
distinction between "genuinely no response ever came back" and "came
back too slowly" is visible in real evidence, not inferred.
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

LONG_TIMEOUT_MS = 120_000  # 120s — real scraper uses 45s; if this
                            # succeeds where 45s fails, it's genuinely
                            # just slow, not blocked

# (council_name, real configured base_url, real monthly-list URL as
# the actual scraper constructs it — month_index=0, current month)
TARGETS = [
    ("Sheffield City Council",
     "https://planningapps.sheffield.gov.uk/online-applications"),
    ("Bassetlaw District Council",
     "https://publicaccess.bassetlaw.gov.uk/online-applications"),
    ("North East Lincolnshire Council",
     "https://planninganddevelopment.nelincs.gov.uk/online-applications"),
    ("Derby City Council",
     "https://eplanning.derby.gov.uk/online-applications"),
]

network_log: list[dict] = []


async def _on_response(response):
    network_log.append({
        "url": response.url,
        "status": response.status,
    })


async def _on_request_failed(request):
    network_log.append({
        "url": request.url,
        "status": "FAILED",
        "failure": request.failure,
    })


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def diagnose_one(browser, name: str, base_url: str):
    print(f"\n{'=' * 70}")
    print(f"PRIORITY 1 DIAGNOSTIC: {name}")
    print("=" * 70)

    monthly_url = (
        f"{base_url}/search.do?action=monthlyList"
        f"&searchCriteria.monthYearIndex=0&searchType=Application"
    )
    print(f"URL: {monthly_url}")

    network_log.clear()
    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()
    page.on("response", lambda r: asyncio.create_task(_on_response(r)))
    page.on("requestfailed", lambda r: asyncio.create_task(_on_request_failed(r)))

    start = datetime.now(timezone.utc)
    real_response = None
    timed_out = False
    error = None

    try:
        real_response = await page.goto(
            monthly_url, wait_until="domcontentloaded", timeout=LONG_TIMEOUT_MS
        )
    except PlaywrightTimeout:
        timed_out = True
    except Exception as e:
        error = str(e)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print(f"Elapsed: {elapsed:.1f}s (long timeout ceiling: {LONG_TIMEOUT_MS/1000:.0f}s)")

    if timed_out:
        print(f"RESULT: Timed out even at {LONG_TIMEOUT_MS/1000:.0f}s "
              f"(6x the real scraper's 45s budget)")
    elif error:
        print(f"RESULT: Navigation error (not a timeout): {error}")
    else:
        status = real_response.status if real_response else None
        print(f"RESULT: Real response received — HTTP {status}, "
              f"after {elapsed:.1f}s")
        title = await page.title()
        print(f"Real page title: {title!r}")

    print(f"\nReal network activity captured during this attempt "
          f"({len(network_log)} entries):")
    if not network_log:
        print("  NONE — not even a single request/response logged. This "
              "points at a genuine network-level block (connection never "
              "even established far enough to register), not just a slow "
              "server.")
    else:
        for entry in network_log[:15]:
            print(f"  {entry}")
        if len(network_log) > 15:
            print(f"  ... and {len(network_log) - 15} more")

    out_png = f"/tmp/priority1_diag_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
        print(f"\nSaved screenshot: {out_png}")
    except Exception as e:
        print(f"\nScreenshot failed (page likely never rendered anything): {e}")

    try:
        html = await page.content()
        out_html = f"/tmp/priority1_diag_{slug(name)}.html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved HTML: {out_html} ({len(html)} chars)")
    except Exception as e:
        print(f"Could not capture HTML: {e}")

    await context.close()

    return {
        "name": name,
        "timed_out_at_120s": timed_out,
        "error": error,
        "network_entries": len(network_log),
        "elapsed": elapsed,
    }


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Priority 1 diagnostic — "
          f"4 councils, tested ALONE (no concurrency) with a 120s timeout "
          f"(vs the real scraper's 45s)\n")

    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for name, base_url in TARGETS:
            result = await diagnose_one(browser, name, base_url)
            results.append(result)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        if r["timed_out_at_120s"]:
            verdict = "STILL TIMES OUT even at 120s — likely a genuine block, not just slow"
        elif r["error"]:
            verdict = f"Real error (not timeout): {r['error'][:80]}"
        elif r["network_entries"] == 0:
            verdict = "Succeeded but captured zero network activity — worth a second look"
        else:
            verdict = f"SUCCEEDED at {r['elapsed']:.1f}s — genuinely just slow, 45s isn't enough"
        print(f"  {r['name']}: {verdict}")

    print("\nDownload the workflow artifact and check the saved screenshots/HTML")
    print("directly — especially for anything that timed out even at 120s,")
    print("since that's the strongest signal of a real block vs just slowness.")


if __name__ == "__main__":
    asyncio.run(main())
