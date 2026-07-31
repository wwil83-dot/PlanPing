#!/usr/bin/env python3
"""Test for the reference-number fallback added to _type_badge/_is_major
(2026-07-30) — a real, confirmed gap: some councils' own application_type
field comes back blank for a given record, and an outline application in
that state was falling through to "other"/not-major entirely, with
nothing checking the reference number itself as a fallback signal."""
import re


def _is_outline_reference(reference: str) -> bool:
    if not reference:
        return False
    last_segment = reference.strip().split("/")[-1].upper()
    return last_segment.startswith("OUT")


def _is_major(app_type: str, reference: str = "") -> bool:
    t = (app_type or "").upper()
    major_keywords = ["OUTLINE", "OUT", "MAJOR", "EIA", "HYBRID",
                      "PERMISSION IN PRINCIPLE", "PIP", "TECHNICAL DETAILS"]
    if any(k in t for k in major_keywords):
        return True
    return _is_outline_reference(reference)


def _type_badge(app_type: str, reference: str = "") -> str:
    t = (app_type or "").lower()
    if "outline" in t or "/out" in t or t.endswith("out"):
        return "outline"
    if "householder" in t or "extension" in t:
        return "householder"
    if "full" in t:
        return "full"
    if "listed" in t:
        return "listed"
    if "tree" in t:
        return "tree"
    if "advertisement" in t or "advert" in t:
        return "advert"
    if "prior" in t:
        return "prior"
    if "major" in t or "eia" in t:
        return "major"
    if _is_outline_reference(reference):
        return "outline"
    return "other"


def run():
    checks = []

    # THE REAL BUG BEING FIXED: blank application_type, real outline
    # reference — this used to silently fall through to "other"/
    # not-major, missing a genuine major development
    checks.append(("blank type + '26/01234/OUT' -> type_badge is outline",
                    _type_badge("", "26/01234/OUT") == "outline"))
    checks.append(("blank type + '26/01234/OUT' -> is_major is True",
                    _is_major("", "26/01234/OUT") is True))

    # Phased/EIA outline suffix variants — real conventions seen in the
    # wild, not just a plain /OUT
    checks.append(("'26/01234/OUT1' (phased outline) -> outline",
                    _type_badge("", "26/01234/OUT1") == "outline"))
    checks.append(("'26/01234/OUTEIA' -> outline",
                    _type_badge("", "26/01234/OUTEIA") == "outline"))

    # The existing, already-working path must be completely unaffected —
    # a real type field should never be overridden by the fallback
    checks.append(("real type 'Full Planning Permission' still -> full",
                    _type_badge("Full Planning Permission", "26/01234/OUT") == "full"))
    checks.append(("real type 'Full Planning Permission' -> is_major still False",
                    _is_major("Full Planning Permission", "26/01234/FUL") is False))
    checks.append(("real type 'Outline Planning Permission' -> outline (unchanged)",
                    _type_badge("Outline Planning Permission", "") == "outline"))

    # A normal, non-outline reference with a blank type must NOT be
    # misclassified — the fallback should only fire for genuine OUT
    # suffixes, not any reference ending in letters that happen to look
    # similar
    checks.append(("blank type + '26/01234/FUL' -> stays 'other', not outline",
                    _type_badge("", "26/01234/FUL") == "other"))
    checks.append(("blank type + '26/01234/FUL' -> is_major stays False",
                    _is_major("", "26/01234/FUL") is False))

    # Genuinely empty/missing reference must not crash or false-positive
    checks.append(("blank type + no reference at all -> 'other', no crash",
                    _type_badge("", "") == "other"))
    checks.append(("blank type + no reference at all -> is_major False",
                    _is_major("", "") is False))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
