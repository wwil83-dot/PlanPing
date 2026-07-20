#!/usr/bin/env python3
"""Quick real-code test for the results-container diagnostic (2026-07-20)."""
import io
import contextlib

from idox_scraper import parse_results_page, _RESULTS_CONTAINER_DIAGNOSED

NO_CONTAINER_HTML = """
<html><head><title>Access Denied</title></head>
<body><p>You have been blocked by our security service.</p></body></html>
"""

WITH_CONTAINER_HTML = """
<html><head><title>Search results</title></head>
<body>
  <ul class="searchresults">
    <li class="searchresult"><a href="/app/1">1</a><h2>Test application</h2></li>
  </ul>
</body></html>
"""


CLOUDFLARE_BLOCK_HTML = """
<html><head><title>Attention Required! | Cloudflare</title></head>
<body><p>Sorry, you have been blocked. Please complete the security check to access this site.</p></body></html>
"""


RATE_LIMIT_BLOCK_HTML = """
<html><head><title>429 Too Many Requests</title></head>
<body><p>429 Too Many Requests Too Many Requests The user has sent too many requests
in a given amount of time.</p></body></html>
"""


def run():
    _RESULTS_CONTAINER_DIAGNOSED.clear()

    # 1. Missing container should print a diagnostic the FIRST time
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        apps, has_next = parse_results_page(NO_CONTAINER_HTML, "https://x.gov.uk", "https://x.gov.uk", "Test Council A")
    out = buf.getvalue()
    check1 = "RESULTS CONTAINER DIAGNOSTIC" in out and "Test Council A" in out and apps == [] and has_next is False

    # 2. Second call for the SAME council should NOT print again (rate-limited)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        parse_results_page(NO_CONTAINER_HTML, "https://x.gov.uk", "https://x.gov.uk", "Test Council A")
    check2 = "RESULTS CONTAINER DIAGNOSTIC" not in buf2.getvalue()

    # 3. A council WITH a real container should never trigger the diagnostic
    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        apps3, _ = parse_results_page(WITH_CONTAINER_HTML, "https://x.gov.uk", "https://x.gov.uk", "Test Council B")
    check3 = "RESULTS CONTAINER DIAGNOSTIC" not in buf3.getvalue() and len(apps3) >= 0

    # 4. A real Cloudflare-style block page should be flagged as LIKELY WAF/BOT BLOCK
    buf4 = io.StringIO()
    with contextlib.redirect_stdout(buf4):
        parse_results_page(CLOUDFLARE_BLOCK_HTML, "https://x.gov.uk", "https://x.gov.uk", "Test Council C")
    out4 = buf4.getvalue()
    check4 = "LIKELY WAF/BOT BLOCK" in out4 and "cloudflare" in out4.lower()

    # 5. A real "429 Too Many Requests" page (seen verbatim in production
    # logs, e.g. Winchester/Cornwall/Babergh/Halton) should now be flagged
    check5_buf = io.StringIO()
    with contextlib.redirect_stdout(check5_buf):
        parse_results_page(RATE_LIMIT_BLOCK_HTML, "https://x.gov.uk", "https://x.gov.uk", "Test Council D")
    out5 = check5_buf.getvalue()
    check5 = "LIKELY WAF/BOT BLOCK" in out5 and ("429" in out5 or "too many requests" in out5.lower())

    checks = [
        ("diagnostic fires on missing container, includes council name", check1),
        ("diagnostic does NOT repeat for same council (rate-limited)", check2),
        ("diagnostic does NOT fire when container present", check3),
        ("WAF signature detection flags a real Cloudflare-style block page", check4),
        ("WAF signature detection flags a real 429 rate-limit page", check5),
    ]
    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
