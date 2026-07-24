#!/usr/bin/env python3
"""Test for civica_scraper.py's URL construction and parsing logic
(2026-07-24) — using text pulled directly from the real St Albans
recon output."""
from datetime import date

from civica_scraper import _build_search_url, _ITEM_TEXT_RE, _parse_date, _extract_postcode


def run():
    checks = []

    # 1. URL construction matches the exact real, confirmed pattern
    # (using decision_date here just to verify the syntax matches the
    # real link found via recon — the scraper itself uses received_date,
    # see the corrected design in scrape())
    url = _build_search_url(
        "https://planningapplications.stalbans.gov.uk/planning",
        "decision_date", date(2026, 7, 12), date(2026, 7, 18),
    )
    expected = (
        "https://planningapplications.stalbans.gov.uk/planning/search-applications"
        "?civica.query.decision_dateFrom=12%2F07%2F2026&civica.query.decision_dateTo=18%2F07%2F2026"
    )
    checks.append(("URL construction matches the real confirmed link exactly", url == expected))

    # 2. Item-text parsing against real text confirmed via recon
    real_texts = [
        ("Planning Application TP/2026/0338 - Valid From 14/07/2026", "TP/2026/0338", "2026-07-14"),
        ("Planning Application 5/2026/1324 - Valid From 09/07/2026", "5/2026/1324", "2026-07-09"),
        ("Planning Application TP/2026/0281 - Valid From 04/06/2026", "TP/2026/0281", "2026-06-04"),
    ]
    all_parsed_ok = True
    for text, expected_ref, expected_date in real_texts:
        m = _ITEM_TEXT_RE.search(text)
        if not m:
            all_parsed_ok = False
            continue
        ref = m.group("ref").strip()
        parsed_date = _parse_date(m.group("date"))
        if ref != expected_ref or parsed_date != expected_date:
            all_parsed_ok = False
    checks.append(("real item text parses to correct reference + date (3 real examples)", all_parsed_ok))

    # 3. Postcode extraction from a real address seen in recon
    real_address = "82 Mount Pleasant Lane Bricket Wood Hertfordshire Al2 3Xd"
    pc = _extract_postcode(real_address)
    checks.append(("postcode extracted from real address text", pc == "AL2 3XD"))

    # 4. Non-matching text doesn't crash / returns None
    checks.append(("non-matching text handled gracefully", _ITEM_TEXT_RE.search("Not a real item") is None))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
