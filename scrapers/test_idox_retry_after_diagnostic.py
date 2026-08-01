#!/usr/bin/env python3
"""Test for the Retry-After diagnostic added 2026-08-01 — real evidence
before building backoff logic, not a guess at whether these councils'
WAFs actually send the header."""
import sys
sys.path.insert(0, ".")


class FakeResponse:
    def __init__(self, status, headers):
        self.status = status
        self.headers = headers


def run():
    checks = []

    import idox_scraper
    idox_scraper._RETRY_AFTER_CHECKED.clear()

    # Real scenario A: server sends Retry-After with a 429
    r = FakeResponse(429, {"retry-after": "30"})
    result = idox_scraper.log_retry_after_if_present(r, "Test Council A")
    checks.append(("429 + Retry-After header -> returns the raw value",
                    result == "30"))

    # Real scenario B: 429 status but genuinely no Retry-After header —
    # equally real, useful evidence (tells us NOT to build honoring
    # logic that assumes the header will always be there)
    r2 = FakeResponse(429, {})
    result2 = idox_scraper.log_retry_after_if_present(r2, "Test Council B")
    checks.append(("429 with NO Retry-After header -> returns None",
                    result2 is None))

    # Normal response — must not fire at all
    r3 = FakeResponse(200, {})
    result3 = idox_scraper.log_retry_after_if_present(r3, "Test Council C")
    checks.append(("normal 200 response -> returns None, no diagnostic",
                    result3 is None))

    # response=None must not crash (some navigations don't return one)
    result4 = idox_scraper.log_retry_after_if_present(None, "Test Council D")
    checks.append(("response=None handled safely, no crash",
                    result4 is None))

    # Once-per-council — a second 429 for the same council shouldn't
    # re-print (matches the established _XXX_DIAGNOSED set pattern used
    # elsewhere in this file), but should still return the real value
    result5 = idox_scraper.log_retry_after_if_present(
        FakeResponse(429, {"retry-after": "60"}), "Test Council A")
    checks.append(("second 429 for an already-checked council still returns the value",
                    result5 == "60"))
    checks.append(("council only added to the checked-set once",
                    len([c for c in idox_scraper._RETRY_AFTER_CHECKED if c == "Test Council A"]) == 1))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
