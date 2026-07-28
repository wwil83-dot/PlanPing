#!/usr/bin/env python3
"""Test for the generalized batch-splitting logic (2026-07-28) — widened
from a hardcoded 2-way split to any N, to support spreading councils
across more, smaller batches (real motivation: reduce aggregate request
volume per run, after evidence of widespread 429/WAF blocks across many
unrelated council domains in a single run)."""


def compute_batch_slice(full_count: int, batch: int, total_batches: int) -> tuple[int, int]:
    """Mirrors the exact slicing logic in idox_scraper.py's __main__
    block — kept here as a standalone, testable function rather than
    importing the whole module (which requires Playwright/DB env vars
    to even parse past its top-level setup)."""
    base_size, remainder = divmod(full_count, total_batches)
    start = (batch - 1) * base_size + min(batch - 1, remainder)
    size = base_size + (1 if batch <= remainder else 0)
    return start, start + size


def run():
    checks = []

    # Exact even split — 208 councils / 4 batches = 52 each, no remainder
    slices = [compute_batch_slice(208, b, 4) for b in range(1, 5)]
    sizes = [end - start for start, end in slices]
    checks.append(("208/4: all 4 batches exactly 52", sizes == [52, 52, 52, 52]))
    checks.append(("208/4: slices are contiguous with no gaps/overlaps",
                    slices[0][1] == slices[1][0] and slices[1][1] == slices[2][0]
                    and slices[2][1] == slices[3][0]))
    checks.append(("208/4: covers the full range exactly once", slices[0][0] == 0 and slices[-1][1] == 208))

    # Uneven split with a remainder — 210 councils / 4 batches should be
    # 53,53,52,52 (remainder distributed across the FIRST batches, not
    # left lopsided on one end)
    slices2 = [compute_batch_slice(210, b, 4) for b in range(1, 5)]
    sizes2 = [end - start for start, end in slices2]
    checks.append(("210/4: remainder distributed across first batches (53,53,52,52)",
                    sizes2 == [53, 53, 52, 52]))
    checks.append(("210/4: still covers the full range exactly once, no gaps",
                    slices2[0][0] == 0 and slices2[-1][1] == 210
                    and all(slices2[i][1] == slices2[i + 1][0] for i in range(3))))

    # Backward compatibility — the original 2-batch behavior should be
    # unchanged for an even split (existing production config uses this)
    old_style = [compute_batch_slice(208, b, 2) for b in (1, 2)]
    checks.append(("208/2 matches the original hardcoded midpoint-split behavior",
                    old_style == [(0, 104), (104, 208)]))

    # Edge case — a single batch (total_batches=1) should just be the
    # whole list, useful for local testing/full runs
    single = compute_batch_slice(208, 1, 1)
    checks.append(("total_batches=1 covers the whole list", single == (0, 208)))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
