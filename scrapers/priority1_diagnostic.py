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

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    # ADDED 2026-08-19 — real evidence: a direct browser test on Sheffield's
    # real site showed NET::ERR_CERT_AUTHORITY_INVALID under active HSTS
    # enforcement (confirmed via a real screenshot — "You cannot visit...
    # because the website uses HSTS", no "proceed anyway" option offered
    # at all). HSTS can enforce certificate validation at a deeper level
    # in the browser's network stack than context-level
    # ignore_https_errors reaches — this flag disables certificate
    # validation at the actual Chromium PROCESS level instead, a
    # genuinely different, more forceful mechanism. Testing directly
    # rather than assuming it fixes anything.
    "--ignore-certificate-errors",
]
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
    # ADDED 2026-08-21 — real, direct evidence: a real browser's
    # DevTools Network tab showed Highland's Remote Address as
    # 46.249.197.178:443 — the EXACT SAME IP confirmed for Derby
    # earlier in this investigation. Same real server. Worth testing
    # directly whether Highland shows the identical zero-network-
    # activity block signature from this runner, confirming (or
    # disproving) that this is the same underlying block, not the
    # separate Cloudflare-category issue an old comment guessed at.
    ("Highland Council",
     "https://wam.highland.gov.uk/wam"),
    # ADDED 2026-08-23 — real, direct evidence just changed the whole
    # theory here: this council was documented as "Idox Cloud migration
    # not yet live" (a portal-doesn't-exist-yet assumption), and
    # idox_waf_recheck.py's own automated check from the UK runner
    # confirmed a genuine 45s timeout — but the person running this
    # project just directly confirmed the SAME URL loads fast and
    # cleanly in their own real browser, even through a Luxembourg VPN.
    # That's the exact same real signature as Derby/Highland/NE
    # Lincolnshire: works fine for any real human/residential
    # connection, silently hangs for automated/cloud traffic. Worth
    # checking directly whether this shows the SAME real zero-network-
    # activity fingerprint, which would mean the original "not yet
    # migrated" theory was wrong all along — it's a live, working
    # portal with a real datacenter-IP block, not a dead one.
    ("St Helens Metropolitan Borough Council",
     "https://publicaccess.sthelens.gov.uk/online-applications"),
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
        # Real evidence check: does this look like the same certificate
        # problem confirmed directly via a real browser test on
        # Sheffield (NET::ERR_CERT_AUTHORITY_INVALID under HSTS)? If the
        # --ignore-certificate-errors launch flag is working, this
        # exception should NOT appear at all — printing it explicitly
        # either way, rather than only inferring from a bare timeout.
        if "CERT" in error.upper() or "SSL" in error.upper() or "TLS" in error.upper():
            print(f"CERTIFICATE-RELATED ERROR CAUGHT: {error}")
            print("(This means --ignore-certificate-errors did NOT fully "
                  "suppress it — worth reading the exact message above.)")

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
          f"{len(TARGETS)} councils, tested ALONE (no concurrency) with a 120s "
          f"timeout (vs the real scraper's 45s)\n")

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
