#!/usr/bin/env python3
"""Test for _diagnose_results_timeout (2026-07-20), added after real
recon evidence showed London Borough of Brent's "Results timeout" was
actually the portal's own "too many results" validation error."""
import asyncio
import io
import contextlib
from unittest.mock import AsyncMock

from idox_scraper import _diagnose_results_timeout, _TOO_MANY_RESULTS_DIAGNOSED


class FakeLocator:
    def __init__(self, text):
        self._text = text

    async def inner_text(self):
        return self._text


class FakePage:
    def __init__(self, title, body_text):
        self._title = title
        self._body_text = body_text

    async def title(self):
        return self._title

    def locator(self, selector):
        return FakeLocator(self._body_text)


BRENT_TOO_MANY_RESULTS_TEXT = (
    "Skip to main content Brent Council ... Planning – Monthly List "
    "Please check the search criteria: Too many results found. Please enter "
    "some more parameters. Search Planning Applications either validated..."
)

NORMAL_TIMEOUT_TEXT = "Skip to main content Some Council Home Planning Search"


async def run():
    _TOO_MANY_RESULTS_DIAGNOSED.clear()

    # 1. Real Brent-style "too many results" text should trigger the diagnostic
    buf = io.StringIO()
    page1 = FakePage("Monthly List", BRENT_TOO_MANY_RESULTS_TEXT)
    with contextlib.redirect_stdout(buf):
        title = await _diagnose_results_timeout(page1, "London Borough of Brent")
    out = buf.getvalue()
    check1 = ("TOO MANY RESULTS DIAGNOSTIC" in out
              and "London Borough of Brent" in out
              and title == "Monthly List")

    # 2. Second timeout for the SAME council should not repeat (rate-limited)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        await _diagnose_results_timeout(page1, "London Borough of Brent")
    check2 = "TOO MANY RESULTS DIAGNOSTIC" not in buf2.getvalue()

    # 3. A genuinely unrelated timeout (no "too many results" text) should
    # NOT trigger this diagnostic — stays a plain "Results timeout"
    buf3 = io.StringIO()
    page3 = FakePage("", NORMAL_TIMEOUT_TEXT)
    with contextlib.redirect_stdout(buf3):
        title3 = await _diagnose_results_timeout(page3, "Some Other Council")
    check3 = "TOO MANY RESULTS DIAGNOSTIC" not in buf3.getvalue() and title3 == ""

    checks = [
        ("real Brent-style 'too many results' text triggers the diagnostic", check1),
        ("diagnostic does NOT repeat for same council (rate-limited)", check2),
        ("unrelated timeout text does NOT falsely trigger this diagnostic", check3),
    ]
    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
