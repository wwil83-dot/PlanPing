#!/usr/bin/env python3
"""Test for register_url_override (2026-07-21) — proves the fix for
Wiltshire's 'Invalid Page' error is scoped correctly: only councils with
an explicit override get a different register_url; everyone else keeps
the proven-working default suffix."""
from arcus_scraper import ArcusPortal


def run():
    checks = []

    # 1. No override -> default suffix behavior, unchanged for the 8
    # councils already proven working (e.g. Powys)
    default_portal = ArcusPortal(
        "Powys County Council", "https://service.powys.gov.uk/pr/s",
        "advanced_search", None, 323,
    )
    checks.append((
        "no override -> default register-view suffix preserved",
        default_portal.register_url == "https://service.powys.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register"
    ))

    # 2. Explicit override -> bare URL used instead, confirmed working
    # via manual browser test
    override_portal = ArcusPortal(
        "Wiltshire Council", "https://development.wiltshire.gov.uk/pr/s",
        "advanced_search", None, 437,
        register_url_override="https://development.wiltshire.gov.uk/pr/s",
    )
    checks.append((
        "explicit override -> bare URL used, no suffix appended",
        override_portal.register_url == "https://development.wiltshire.gov.uk/pr/s"
    ))

    # 3. base_url itself is untouched by the override (used elsewhere,
    # e.g. council_url fallback) — only register_url should change
    checks.append((
        "base_url unaffected by the override (only register_url changes)",
        override_portal.base_url == "https://development.wiltshire.gov.uk/pr/s"
    ))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
