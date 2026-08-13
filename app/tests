#!/usr/bin/env python3
"""Test for keyword search normalization (2026-08-11) — applying the
same empty-string-vs-None lesson already learned and fixed once this
session for status/app_type. A keyword box left blank, or containing
only whitespace, must never silently exclude every result.

Imports the real function from app.main — not a duplicated copy — so
this test actually catches a real regression if the logic ever
changes, rather than checking a stale mirror of it."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import _normalize_keyword as normalize_keyword


def run():
    checks = []

    checks.append(("None input -> None", normalize_keyword(None) is None))
    checks.append(("empty string '' -> None", normalize_keyword("") is None))
    checks.append(("whitespace-only '   ' -> None (not a literal space filter)",
                    normalize_keyword("   ") is None))
    checks.append(("real keyword 'solar' -> 'solar'",
                    normalize_keyword("solar") == "solar"))
    checks.append(("real keyword with surrounding whitespace -> stripped",
                    normalize_keyword("  barn conversion  ") == "barn conversion"))
    checks.append(("keyword with internal spaces preserved",
                    normalize_keyword("data centre") == "data centre"))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
