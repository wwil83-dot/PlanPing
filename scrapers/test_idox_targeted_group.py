#!/usr/bin/env python3
"""Test for the targeted-group filtering added 2026-07-31 — a small,
isolated job for the confirmed WAF/429-affected councils, built after
a blanket per-request delay across all 216 councils was tried and rolled
back (real evidence: near-identical hit counts before/after, same
councils affected both nights, while costing real runtime margin)."""
import sys
sys.path.insert(0, ".")


def run():
    checks = []

    from idox_scraper import TARGETED_GROUP
    from idox_councils import IDOX_COUNCILS

    real_names = {c[0] for c in IDOX_COUNCILS}

    checks.append(("TARGETED_GROUP has exactly 13 councils",
                    len(TARGETED_GROUP) == 13))
    checks.append(("every targeted council name resolves against idox_councils.py",
                    TARGETED_GROUP.issubset(real_names)))

    # Simulate the actual filtering logic from main()
    filtered = [c for c in IDOX_COUNCILS if c[0] in TARGETED_GROUP]
    checks.append(("filtering IDOX_COUNCILS by the group yields exactly 13",
                    len(filtered) == 13))
    checks.append(("no duplicate councils in the filtered result",
                    len({c[0] for c in filtered}) == 13))

    # Confirm it's genuinely scoped to WAF/429 cases, not general failures —
    # spot-check a couple of councils known to fail for OTHER reasons
    # (Cloudflare challenge, IDX002) are deliberately NOT included
    other_failure_councils = {"Doncaster Metropolitan Borough Council",
                               "Rother District Council", "Trafford Council"}
    checks.append(("councils with different failure modes are NOT in the group",
                    TARGETED_GROUP.isdisjoint(other_failure_councils)))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
