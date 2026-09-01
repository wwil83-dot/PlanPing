#!/usr/bin/env python3
"""
PlanFind — Idox candidate batch verification (2026-09-01).

19 councils from the user's newest manual recon list, all labelled
"(IDOX)" — but Fylde taught us a hard lesson: a URL LOOKING like Idox
isn't the same as CONFIRMING it. This loads each real candidate URL
and checks for genuine Idox markers (the standard weekly-list results
table, or at minimum a real, unblocked page) before any of these get
added to idox_councils.py's live IDOX_COUNCILS list.

Real, confirmed real risk categories to watch for in the output:
  - WAF/429 blocks (this project has fought this extensively elsewhere
    in the Idox family)
  - Redirects to an unrelated domain (happened with Cheltenham/
    Lewisham/Fylde earlier in this project)
  - Genuinely different Idox skin/variant that doesn't match standard
    selectors
  - Braintree specifically noted by the user as sitting behind
    Cloudflare — worth extra scrutiny
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
    ("Bromsgrove and Redditch",
     "https://publicaccess.bromsgroveandredditch.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Gloucester City Council",
     "https://publicaccess.gloucester.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Monmouthshire County Council",
     "https://planningonline.monmouthshire.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Newport City Council",
     "https://publicaccess.newport.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Cardiff Council (IdoxCloud)",
     "https://www.cardiffidoxcloud.wales/publicaccess/search.do?action=weeklyList"),
    ("Merthyr Tydfil County Borough Council",
     "https://publicaccess.merthyr.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Rhondda Cynon Taf County Borough Council",
     "https://planonline.rctcbc.gov.uk/online-applications/search.do?action=weeklyList"),
    ("City and County of Swansea",
     "https://property.swansea.gov.uk/online-applications/search.do?action=weeklyList"),
    ("South Gloucestershire Council",
     "https://developments.southglos.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Oxford City Council",
     "https://public.oxford.gov.uk/online-applications/search.do?action=weeklyList"),
    ("West Berkshire Council",
     "https://publicaccess.westberks.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Buckinghamshire Council",
     "https://publicaccess.buckinghamshire.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Three Rivers District Council",
     "https://www3.threerivers.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Watford Borough Council",
     "https://pa.watford.gov.uk/publicaccess/search.do?action=weeklyList"),
    ("London Borough of Barnet",
     "https://publicaccess.barnet.gov.uk/online-applications/search.do?action=weeklyList"),
    ("London Borough of Enfield",
     "https://planningandbuildingcontrol.enfield.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Harlow Council",
     "https://planningonline.harlow.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Uttlesford District Council",
     "https://publicaccess.uttlesford.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Braintree District Council (behind Cloudflare per user note)",
     "https://publicaccess.braintree.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Tendring District Council",
     "https://idox.tendringdc.gov.uk/online-applications/search.do?action=weeklyList"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")


async def recon_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    context = await browser.new_context(**CONTEXT_OPTIONS)
    page = await context.new_page()

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass
    except Exception as e:
        print(f"  ⚠ Navigation error (real timeout/block signature): {type(e).__name__}: {e!r}")
        await context.close()
        return {"name": name, "status": "NAV_ERROR"}

    status = response.status if response else None
    title = await page.title()
    final_url = page.url

    print(f"  Real HTTP status: {status}")
    print(f"  Real page title: {title!r}")
    print(f"  Real final URL: {final_url}")

    # Real Idox marker check: standard weekly-list results table, or at
    # minimum a genuine, unblocked Idox-style page (title commonly
    # contains "Planning" or "Public Access" or similar).
    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text())[:400]
    except Exception:
        pass

    real_idox_markers = any(
        marker in body_text for marker in
        ("Weekly List", "Simple Search", "Advanced Search", "Application Search")
    ) or "search.do" in final_url

    looks_blocked = any(
        marker in (title + body_text) for marker in
        ("Access Denied", "403 Forbidden", "Attention Required", "cloudflare", "Cloudflare")
    )

    verdict = "LOOKS REAL" if real_idox_markers and not looks_blocked else (
        "POSSIBLY BLOCKED/WAF" if looks_blocked else "UNCERTAIN — needs a look"
    )

    print(f"  Real body text (first 400 chars): {body_text!r}")
    print(f"  VERDICT: {verdict}")

    await context.close()
    return {"name": name, "status": status, "title": title, "final_url": final_url, "verdict": verdict}


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Idox candidate batch verification "
          f"— {len(TARGETS)} candidates\n")

    # REAL FIX (2026-09-01) — first run fired all 20 navigations back-
    # to-back with zero delay and hit a wall of navigation timeouts
    # from item 8 onward (Swansea and everything after), while the
    # first 7 were a genuine mix of real successes and real one-off
    # failures. That shape — clean-ish start, then EVERYTHING fails
    # identically regardless of destination — matches cumulative
    # rate-limiting across Idox's shared hosting platform, not any one
    # council's own WAF. This project already solved exactly this
    # problem for idox_scraper.py's --targeted mode via CONCURRENCY=1 +
    # REQUEST_DELAY_SECONDS=5 — applying the same proven pacing here,
    # which this script skipped the first time.
    REQUEST_DELAY_SECONDS = 6

    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")

        for i, (name, url) in enumerate(TARGETS):
            if i > 0:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
            result = await recon_one(browser, name, url)
            results.append(result)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        print(f"  {r.get('verdict', 'ERROR'):25s} {r['name']}")

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
