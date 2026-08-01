#!/usr/bin/env python3
"""Test for the bounded, jittered backoff retry added 2026-08-01 — only
built after the internal per-council budget checkpoint made it safe
(retries add real wall-clock time; without that fix a batch running
long from retries could crash the whole run the way Cornwall did).
Deliberately capped small (MAX_429_RETRIES=2 default) rather than
open-ended, since real evidence from the targeted-group test suggests
at least some of these blocks aren't purely rate/timing-based."""
import asyncio
import sys
sys.path.insert(0, ".")


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}


class FakePage:
    """Simulates a sequence of responses across repeated goto() calls —
    e.g. [429, 429, 200] simulates two blocks then a real success."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    async def goto(self, url, wait_until=None, timeout=None):
        self.call_count += 1
        return self.responses.pop(0)


def run():
    checks = []
    import idox_scraper
    idox_scraper._RETRY_AFTER_CHECKED.clear()
    idox_scraper.REQUEST_DELAY_SECONDS = 0  # don't slow the test down
    idox_scraper.BACKOFF_BASE_SECONDS = 0.01  # tiny, fast test delays

    # Real scenario: two 429s then a genuine success — should retry
    # twice and return the final 200
    page = FakePage([
        FakeResponse(429, {"retry-after": "0.01"}),
        FakeResponse(429, {}),
        FakeResponse(200, {}),
    ])
    idox_scraper.MAX_429_RETRIES = 2
    response = asyncio.run(idox_scraper.goto_with_backoff(page, "http://x", "Test A"))
    checks.append(("eventually succeeds after 2 retries -> returns the 200",
                    response.status == 200))
    checks.append(("made exactly 3 real navigation attempts",
                    page.call_count == 3))

    # Real scenario: PERSISTENT 429s beyond the cap — must stop at
    # MAX_429_RETRIES, not retry forever (the real, bounded-cost
    # guarantee this whole feature depends on)
    page2 = FakePage([FakeResponse(429, {}) for _ in range(10)])
    idox_scraper.MAX_429_RETRIES = 2
    response2 = asyncio.run(idox_scraper.goto_with_backoff(page2, "http://x", "Test B"))
    checks.append(("persistent 429s -> stops at the cap, returns the last 429",
                    response2.status == 429))
    checks.append(("exactly MAX_429_RETRIES+1 attempts made, not unlimited",
                    page2.call_count == 3))  # 1 initial + 2 retries

    # A clean, immediate success must not retry at all
    page3 = FakePage([FakeResponse(200, {})])
    response3 = asyncio.run(idox_scraper.goto_with_backoff(page3, "http://x", "Test C"))
    checks.append(("clean 200 on first try -> no retries, single call",
                    page3.call_count == 1 and response3.status == 200))

    # Retry-After parsing — both real formats servers actually use
    checks.append(("Retry-After as plain seconds parses correctly",
                    idox_scraper._parse_retry_after_seconds("30") == 30.0))
    checks.append(("Retry-After with garbage doesn't crash, returns None",
                    idox_scraper._parse_retry_after_seconds("not-a-real-value") is None))
    checks.append(("Retry-After absent returns None",
                    idox_scraper._parse_retry_after_seconds(None) is None))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
