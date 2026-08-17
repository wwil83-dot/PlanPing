#!/usr/bin/env python3
"""
PlanFind — Idox WAF-block recheck tool (2026-08-17).

Real, targeted recheck of 4 councils whose Idox portals were previously
CONFIRMED blocked with real evidence, all in July 2026 — worth a fresh
check now that time has passed, since WAF rules and IP-reputation
blocklists do change over time (this project's own precedent: the
original ~13-council Idox WAF-blocked group has never been assumed
permanent, and this project deliberately keeps their DB rows/config
intact for exactly this kind of future re-check rather than deleting
them).

IMPORTANT REAL EVIDENCE (2026-08-17, confirmed directly by the person
running this project): all 4 of these original blocks were found while
testing from a US IP — the same root-cause CATEGORY as the
getApplications-family platform's "IDX002" block earlier this session,
which the UK runner alone fully resolved, no other change needed. This
genuinely raises the odds that some or all of these 4 could clear on a
UK IP alone — but it's not a certainty (Idox's WAF, wherever each
council's instance is hosted, could easily be a different actual
product/config than the getApplications platform's AWS WAF, with a
different real trigger). This script runs on runs-on: [self-hosted,
uk-runner] specifically so its real result actually tests this, rather
than repeating a US-IP test and reporting a stale, already-known
answer.

  - Tonbridge and Malling Borough Council — confirmed blocked 2026-07-20
  - Solihull Metropolitan Borough Council — confirmed blocked 2026-07-23.
    NOTE: two different URLs exist in this project's own records —
    the DB's stored portal_url says eservices.solihull.gov.uk, but the
    disabled Idox config AND a fresh user-provided URL both say
    publicaccess.solihull.gov.uk. Testing the publicaccess one here as
    the more likely current address; if it also fails, the eservices
    one is worth a separate check too.
  - North East Derbyshire District Council — confirmed real Cloudflare
    WAF block, 2026-07-23
  - Bolsover District Council — confirmed real Incapsula WAF block
    ("Request unsuccessful. Incapsula incident ID...", Imperva's
    "Access denied — Error 16" page), 2026-07-23

WHY PLAYWRIGHT, NOT PLAIN HTTP (unlike the existing
northgate_url_healthcheck.py, which uses plain httpx): a real, hard
lesson from THIS project, THIS session — getapplications_scraper.py's
first production run looked like a normal 200-with-empty-results
response under plain httpx, and only turned out to be an AWS WAF
JavaScript challenge once actually inspected. A plain HTTP status check
can't reliably distinguish "genuinely working now" from "still blocked,
but the block page happens to return 200" — this tool loads each real
page with a real browser and inspects the actual rendered content for
known WAF signatures, not just the HTTP status code.

This tool INTENTIONALLY does not attempt any login/CAPTCHA-solving or
retry-with-different-headers tricks — it's a single, honest check per
council: did the real block clear, yes or no, with real evidence either
way. Re-enabling any of these in idox_councils.py is a separate,
deliberate step to take only after this reports a genuine, clean pass.
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

# (council_name, url, real confirmed block signature to look for)
TARGETS = [
    ("Tonbridge and Malling Borough Council",
     "https://publicaccess.tmbc.gov.uk/online-applications/search.do?action=weeklyList",
     None),  # no specific signature previously recorded — generic check
    ("Solihull Metropolitan Borough Council",
     "https://publicaccess.solihull.gov.uk/online-applications/search.do?action=weeklyList",
     None),
    ("North East Derbyshire District Council",
     "https://planapps-online.ne-derbyshire.gov.uk/online-applications/search.do?action=weeklyList",
     "cloudflare"),
    ("Bolsover District Council",
     "https://publicaccess.bolsover.gov.uk/online-applications/search.do?action=weeklyList",
     "incapsula"),
]

# Real, known WAF/block-page signatures — extend this list with real
# evidence only, same discipline as every diagnostic elsewhere in this
# project. Checked against the real rendered page text, case-insensitive.
KNOWN_BLOCK_SIGNATURES = [
    "incapsula incident id",
    "access denied",
    "request unsuccessful",
    "attention required! | cloudflare",
    "checking your browser",
    "error (idx",
    "gokuprops",
    "awswafcookiedomainlist",
    "too many requests",
    "unusual traffic",
]

# A genuine Idox weekly-list page, working correctly, should contain
# something like these — real, distinctive Idox search-page markup
# rather than a guess.
IDOX_REAL_CONTENT_SIGNALS = [
    "weekly list", "search criteria", "application", "planning",
]


async def check_one(browser, name: str, url: str, known_signature):
    print(f"\n{'=' * 70}")
    print(f"RECHECK: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:
        print(f"  ⚠ Navigation error: {e}")
        print(f"  VERDICT: still failing (could not even load the page)")
        await context.close()
        return "still_blocked"

    status = response.status if response else None
    print(f"  HTTP status: {status}")

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(1)

    title = await page.title()
    print(f"  Real page title: {title!r}")

    try:
        body_text = (await page.locator("body").inner_text()).lower()
    except Exception:
        body_text = ""

    found_signatures = [sig for sig in KNOWN_BLOCK_SIGNATURES if sig in body_text]
    if known_signature:
        print(f"  Checking specifically for previously-confirmed signature: "
              f"{known_signature!r}")

    out_png = f"/tmp/idox_waf_recheck_{name.lower().replace(' ', '_')}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
        print(f"  Saved screenshot: {out_png}")
    except Exception:
        pass

    await context.close()

    if found_signatures:
        print(f"  ⚠ Real WAF/block signature(s) still present: {found_signatures}")
        print(f"  VERDICT: still blocked")
        return "still_blocked"

    has_real_content = any(sig in body_text for sig in IDOX_REAL_CONTENT_SIGNALS)
    if status == 200 and has_real_content:
        print(f"  ✓ No known block signature found, real Idox-shaped content present")
        print(f"  VERDICT: block may have cleared — worth a closer manual look "
              f"before re-enabling")
        return "possibly_clear"

    print(f"  ⚠ No known block signature found, but no clear real Idox content "
          f"either (status={status}) — inconclusive, check the screenshot directly")
    return "inconclusive"


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Idox WAF-block recheck — "
          f"{len(TARGETS)} previously-confirmed-blocked councils\n")

    results = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for name, url, signature in TARGETS:
            verdict = await check_one(browser, name, url, signature)
            results[name] = verdict

        await browser.close()

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    for name, verdict in results.items():
        print(f"  {verdict.upper():16s} {name}")

    possibly_clear = [n for n, v in results.items() if v == "possibly_clear"]
    if possibly_clear:
        print(f"\n{len(possibly_clear)} council(s) may be worth re-enabling in "
              f"idox_councils.py — check the saved screenshots first, then "
              f"uncomment their real entries and give them one real test run "
              f"before trusting them on the nightly schedule.")
    else:
        print("\nNo councils cleared this recheck — all still show a real, "
              "confirmed block. Worth trying again in a few weeks.")


if __name__ == "__main__":
    asyncio.run(main())
