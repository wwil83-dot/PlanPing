#!/usr/bin/env python3
"""
PlanFind — Webshare Static Residential proxy test, Derby + North East
Lincolnshire only (2026-08-21).

PURPOSE: real, direct test of whether routing through a genuine
residential-ASN IP gets past the confirmed datacenter block on these
two councils specifically. Real evidence so far: both councils hang
with ZERO network activity from every cloud/datacenter IP tested
(DigitalOcean UK, Azure US via ubuntu-latest) — but load completely
normally from a real home ISP AND from Surfshark VPN in two different
countries. That pattern points at ASN-based datacenter detection, not
country or session-based blocking — meaning a residential-ASN proxy,
regardless of which country it's physically in, should plausibly get
past it.

Deliberately NOT testing Sheffield or Bassetlaw — their confirmed real
problem is a broken TLS certificate on their own server (NET::
ERR_CERT_AUTHORITY_INVALID under active HSTS enforcement, confirmed
directly via a real browser). A proxy changes where the request comes
FROM, not whether the target server's own certificate is valid.

Uses the EXACT SAME target URL that previously hung with zero network
activity from every cloud IP tested (priority1_diagnostic.py) — the
real monthlyList search page, not a simplified test URL — for a fair,
direct comparison against that existing evidence.

Credentials read from environment variables — never hardcoded, same
convention as SCRAPERAPI_KEY/SUPABASE_URL/SUPABASE_KEY throughout this
project. Real Webshare Static Residential plan confirmed: username/
password authentication (not IP whitelist — necessary since GitHub
Actions runners don't have a fixed IP to register).

ARCHITECTURE NOTE: unlike every Playwright-based diagnostic in this
project, this one deliberately does NOT need the self-hosted UK
runner — the whole point of a proxy is that IT provides the real exit
IP, not whichever machine initiates the connection. ubuntu-latest is
fine and appropriate here, same reasoning as the ScraperAPI test.
"""
import asyncio
import os
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

PROXY_HOST = os.environ.get("WEBSHARE_PROXY_HOST", "")
PROXY_PORT = os.environ.get("WEBSHARE_PROXY_PORT", "")
PROXY_USERNAME = os.environ.get("WEBSHARE_PROXY_USERNAME", "")
PROXY_PASSWORD = os.environ.get("WEBSHARE_PROXY_PASSWORD", "")

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

LONG_TIMEOUT_MS = 60_000  # real evidence: if the proxy genuinely works,
                           # this should succeed fast — 60s is already
                           # generous, matching priority1_diagnostic.py's
                           # own reduced timeout once the readonly-field
                           # lesson was learned elsewhere in this project

# Same real, confirmed URLs used in priority1_diagnostic.py — the exact
# thing that hung with zero network activity from every cloud IP so far
TARGETS = [
    ("Derby City Council",
     "https://eplanning.derby.gov.uk/online-applications/search.do"
     "?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
    ("North East Lincolnshire Council",
     "https://planninganddevelopment.nelincs.gov.uk/online-applications/search.do"
     "?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application"),
]

network_log: list[dict] = []


async def _on_response(response):
    network_log.append({"url": response.url, "status": response.status})


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


async def test_one(browser, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"WEBSHARE RESIDENTIAL PROXY TEST: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    network_log.clear()
    context = await browser.new_context(
        proxy={
            "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
            "username": PROXY_USERNAME,
            "password": PROXY_PASSWORD,
        },
        **CONTEXT_OPTIONS,
    )
    page = await context.new_page()
    page.on("response", lambda r: asyncio.create_task(_on_response(r)))

    # Real, direct confirmation of what IP this session is actually
    # exiting through — before even touching the real target, so we
    # know for certain the proxy is genuinely in effect, not silently
    # falling through to the runner's own IP.
    try:
        ip_resp = await page.goto("https://ipv4.webshare.io/", timeout=20_000)
        exit_ip = (await page.locator("body").inner_text()).strip()
        print(f"Real exit IP via this proxy: {exit_ip}")
    except Exception as e:
        print(f"⚠ Could not confirm exit IP (continuing anyway): {e}")

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
    print(f"\nElapsed: {elapsed:.1f}s")

    if timed_out:
        print(f"RESULT: Timed out at {LONG_TIMEOUT_MS/1000:.0f}s — still blocked, "
              f"even through a residential IP")
    elif error:
        print(f"RESULT: Navigation error (not a timeout): {error}")
    else:
        status = real_response.status if real_response else None
        print(f"RESULT: Real response received — HTTP {status}, after {elapsed:.1f}s")
        title = await page.title()
        print(f"Real page title: {title!r}")
        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:1000]
        except Exception:
            pass
        print(f"Visible body text (first 1000 chars): {body_text!r}")

    print(f"\nReal network activity captured ({len(network_log)} entries):")
    if not network_log:
        print("  NONE — same signature as every cloud IP tested so far, "
              "would mean the block isn't purely ASN-based after all")
    else:
        for entry in network_log[:10]:
            print(f"  {entry}")

    out_png = f"/tmp/webshare_proxy_test_{slug(name)}.png"
    try:
        await page.screenshot(path=out_png, full_page=True)
        print(f"Saved screenshot: {out_png}")
    except Exception:
        pass

    await context.close()
    return {"name": name, "timed_out": timed_out, "error": error,
            "network_entries": len(network_log)}


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Webshare Static Residential "
          f"proxy test — Derby + North East Lincolnshire only\n")
    print("Sheffield/Bassetlaw deliberately NOT tested — their confirmed problem "
          "is a broken certificate on their own server, not something a proxy "
          "can fix.\n")

    if not all([PROXY_HOST, PROXY_PORT, PROXY_USERNAME, PROXY_PASSWORD]):
        print("ERROR: WEBSHARE_PROXY_HOST / WEBSHARE_PROXY_PORT / "
              "WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD must all be set "
              "as environment variables first.")
        return

    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")

        for name, url in TARGETS:
            result = await test_one(browser, name, url)
            results.append(result)

        await browser.close()

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        if r["timed_out"]:
            verdict = "STILL BLOCKED — same as every cloud IP tested so far"
        elif r["error"]:
            verdict = f"Real error (not timeout): {r['error'][:80]}"
        elif r["network_entries"] == 0:
            verdict = "Succeeded but captured zero network activity — worth a closer look"
        else:
            verdict = "SUCCEEDED — a residential ASN genuinely gets past this block"
        print(f"  {r['name']}: {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
