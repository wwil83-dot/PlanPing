#!/usr/bin/env python3
"""Test for the request-pacing helper added 2026-07-30 — a real,
deliberate pause before every page request, meant to slow the aggregate
request rate without touching the batch/schedule structure at all
(real evidence: WAF/429 blocks clustering across several different,
unrelated councils within a tight window of the same run, consistent
with something tracking aggregate volume rather than a strict
per-council threshold)."""
import asyncio
import importlib
import os
import time


def run():
    checks = []

    # Default delay (1.5s) — confirm it actually pauses roughly that long,
    # not just a no-op
    os.environ.pop("REQUEST_DELAY_SECONDS", None)
    import idox_scraper
    importlib.reload(idox_scraper)

    start = time.monotonic()
    asyncio.run(idox_scraper.pace_request())
    elapsed = time.monotonic() - start
    checks.append(("default delay (~1.5s) actually pauses that long",
                    1.3 <= elapsed <= 2.0))

    # Env var override — confirm a custom value is genuinely respected
    os.environ["REQUEST_DELAY_SECONDS"] = "0.3"
    importlib.reload(idox_scraper)
    start = time.monotonic()
    asyncio.run(idox_scraper.pace_request())
    elapsed = time.monotonic() - start
    checks.append(("custom REQUEST_DELAY_SECONDS=0.3 is respected",
                    0.2 <= elapsed <= 0.6))

    # Zero — confirm it can be fully disabled for fast local testing,
    # not stuck with a minimum forced delay
    os.environ["REQUEST_DELAY_SECONDS"] = "0"
    importlib.reload(idox_scraper)
    start = time.monotonic()
    asyncio.run(idox_scraper.pace_request())
    elapsed = time.monotonic() - start
    checks.append(("REQUEST_DELAY_SECONDS=0 skips the delay entirely",
                    elapsed < 0.1))

    os.environ.pop("REQUEST_DELAY_SECONDS", None)

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
