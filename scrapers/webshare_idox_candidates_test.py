#!/usr/bin/env python3
"""
PlanFind — Webshare Static Residential proxy test, parked Idox
candidates (2026-09-01).

PURPOSE: real, direct test of whether routing through a genuine
residential-ASN IP gets past whatever's blocking 15 of the 20 Idox
candidates from idox_candidate_verify.py. Real evidence so far: those
15 timed out identically across two separate runs (one with, one
without inter-request pacing) — a deterministic, per-target pattern
that pacing didn't change at all, pointing away from rate-limiting and
toward a per-target network-level block (most plausibly the same
"known hosting/datacenter-provider IP range" WAF rule category this
project already confirmed elsewhere, e.g. Derby/North East
Lincolnshire — see webshare_priority1_test.py's own module docstring
for that original evidence trail).

Reuses the exact same proven architecture as
webshare_priority1_test.py (IP verification before the real test,
same timeout/reporting shape) — only the target list differs. Testing
a representative sample of 5 (not all 15) first, to confirm the
approach works before spending more proxy bandwidth on the rest.

Credentials read from environment variables — never hardcoded, same
convention as every other credential in this project. Update the
GitHub Secrets (WEBSHARE_PROXY_HOST/PORT/USERNAME/PASSWORD) with
today's live values from the Webshare dashboard before running this —
the original secrets may be stale from the August test.

ARCHITECTURE NOTE: deliberately does NOT need the self-hosted UK
runner — the whole point of a proxy is that IT provides the real exit
IP. ubuntu-latest is fine and appropriate here.
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

LONG_TIMEOUT_MS = 60_000

# 5 of the 15 councils that timed out identically (regardless of
# pacing) in idox_candidate_verify.py — a representative sample before
# spending more proxy bandwidth on the rest.
TARGETS = [
    ("Gloucester City Council",
     "https://publicaccess.gloucester.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Newport City Council",
     "https://publicaccess.newport.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Cardiff Council (IdoxCloud)",
     "https://www.cardiffidoxcloud.wales/publicaccess/search.do?action=weeklyList"),
    ("City and County of Swansea",
     "https://property.swansea.gov.uk/online-applications/search.do?action=weeklyList"),
    ("Oxford City Council",
     "https://public.oxford.gov.uk/online-applications/search.do?action=weeklyList"),
]

network_log: list[dict] = []


async def _on_response(response):
    network_log.append({"url": response.url, "status": response.status})


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("(", "").replace(")", "")


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

    try:
        await page.goto("https://ipv4.webshare.io/", timeout=20_000)
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
        print("  NONE — same signature as the original blocked attempts, "
              "would mean this isn't purely a datacenter-IP block after all")
    else:
        for entry in network_log[:10]:
            print(f"  {entry}")

    out_png = f"/tmp/webshare_idox_test_{slug(name)}.png"
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
          f"proxy test — 5 parked Idox candidates\n")

    if not all([PROXY_HOST, PROXY_PORT, PROXY_USERNAME, PROXY_PASSWORD]):
        print("ERROR: WEBSHARE_PROXY_HOST / WEBSHARE_PROXY_PORT / "
              "WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD must all be set "
              "as environment variables first — check these are today's live "
              "values from the Webshare dashboard, not stale from an earlier test.")
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
            verdict = "STILL BLOCKED — same as the original attempt"
        elif r["error"]:
            verdict = f"Real error (not timeout): {r['error'][:80]}"
        elif r["network_entries"] == 0:
            verdict = "Succeeded but captured zero network activity — worth a closer look"
        else:
            verdict = "SUCCEEDED — a residential ASN genuinely gets past this block"
        print(f"  {r['name']}: {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
