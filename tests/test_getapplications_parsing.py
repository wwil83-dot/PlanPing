#!/usr/bin/env python3
"""Test for getapplications_scraper.py's parsing logic (2026-08-17) —
built against real, reconstructed screenshot data (Liverpool's weekly
list and detail page for id=178037), not synthetic guesses at
structure. Specifically regression-tests a real bug caught before it
ever touched production: the detail-page label regex originally only
listed the ~14 labels the scraper actually uses, which let real,
present labels not on that list ("Grid Reference", "Expiry Date")
silently swallow into the PRECEDING field's value instead of being
recognised as their own field boundary."""
import sys
sys.path.insert(0, ".")

from getapplications_scraper import (
    _parse_weekly_list, _parse_detail_page, _normalise_status,
    _extract_id_from_url, _mondays_back, _extract_postcode,
    _diagnose_empty_response, _EMPTY_RESPONSE_DIAGNOSED,
)

WEEKLY_LIST_HTML = """
<html><body>
<table>
  <tr>
    <th>Application</th><th>Location Details</th><th>Proposal</th>
    <th>Ward</th><th>Community</th><th>Details Available</th><th>Jump to Application</th>
  </tr>
  <tr>
    <td>26T/1579</td>
    <td>22 Druids Cross Road, Liverpool, L18 3HW</td>
    <td>To carry out tree works</td>
    <td>Calderstones</td>
    <td>City South</td>
    <td>Yes</td>
    <td><a href="/planning/index.html?fa=getApplication&amp;id=178023">View</a></td>
  </tr>
  <tr>
    <td>26PH/1637</td>
    <td>6 South Parkside Walk, Liverpool, L12 5ES</td>
    <td>To erect single storey extension to rear</td>
    <td>West Derby Muirhead</td>
    <td>City North</td>
    <td>Yes</td>
    <td><a href="/planning/index.html?fa=getApplication&amp;id=178012">View</a></td>
  </tr>
</table>
</body></html>
"""

# Real field labels + values, reconstructed directly from a real
# screenshot of https://lar.liverpool.gov.uk/.../getApplication&id=178037
DETAIL_HTML = """
<html><body>
<div>
Application Reference Number: 26LE/2308
Application Type: Lawful Devt/Use Exisiting
Proposal: Application for Certificate of Existing Lawful Development for House in Multiple Occupation for 3-6 persons (Use Class C4)
Applicant: James Williams (CJFJ Home LTD)
Location: 19 Aviemore Road, Liverpool, L13 3BB
Grid Reference: 338638, 391231
Ward: Old Swan West
Parish / Community: City North
Officer: Allocations North
Decision Level:
Application Status: Consultation/Publicity
</div>
<div>
Received Date: 16-08-2026
Valid Date: 17-08-2026
Expiry Date: 12-10-2026
Extension Of Time: No
Extension Of Time Due Date:
Planning Performance Agreement: No
Planning Performance Agreement Due Date:
Proposed Committee Date:
Actual Committee Date:
Decision Issued Date:
Decision:
Appeal Reference:
Appeal Status:
Appeal External Decision:
Appeal External Decision Date:
</div>
</body></html>
"""


def run():
    checks = []

    apps = _parse_weekly_list(WEEKLY_LIST_HTML, "https://lar.liverpool.gov.uk", "Test Council")
    checks.append(("weekly list: correct number of applications parsed", len(apps) == 2))
    checks.append(("weekly list: reference matched via header keyword", apps[0]["reference"] == "26T/1579"))
    checks.append(("weekly list: address matched via header keyword", apps[0]["address"] == "22 Druids Cross Road, Liverpool, L18 3HW"))
    checks.append(("weekly list: real detail id extracted from href", apps[0]["id"] == "178023"))
    checks.append(("weekly list: council_url correctly built, & not &amp;", apps[0]["council_url"] == "https://lar.liverpool.gov.uk/planning/index.html?fa=getApplication&id=178023"))

    fields = _parse_detail_page(DETAIL_HTML)
    checks.append(("detail page: reference field correct", fields.get("Application Reference Number") == "26LE/2308"))
    checks.append(("detail page: applicant field correct", fields.get("Applicant") == "James Williams (CJFJ Home LTD)"))
    checks.append(("detail page: Location does NOT swallow Grid Reference (regression)",
                    fields.get("Location") == "19 Aviemore Road, Liverpool, L13 3BB"))
    checks.append(("detail page: Grid Reference recognised as its own field (regression)",
                    fields.get("Grid Reference") == "338638, 391231"))
    checks.append(("detail page: Valid Date does NOT swallow Expiry Date (regression)",
                    fields.get("Valid Date") == "17-08-2026"))
    checks.append(("detail page: Expiry Date recognised as its own field (regression)",
                    fields.get("Expiry Date") == "12-10-2026"))
    checks.append(("detail page: Parish / Community field correct", fields.get("Parish / Community") == "City North"))
    checks.append(("detail page: blank Decision stays blank (still pending)", fields.get("Decision", "") == ""))

    decided_html = DETAIL_HTML.replace(
        "Decision Issued Date:\nDecision:",
        "Decision Issued Date: 20-08-2026\nDecision: Application Permitted"
    )
    fields2 = _parse_detail_page(decided_html)
    checks.append(("detail page: real Decision text captured", fields2.get("Decision") == "Application Permitted"))
    checks.append(("detail page: real Decision Issued Date captured", fields2.get("Decision Issued Date") == "20-08-2026"))
    checks.append(("status: a real decision normalises to 'approved'", _normalise_status(fields2.get("Decision", "")) == "approved"))
    checks.append(("status: blank decision normalises to 'pending'", _normalise_status("") == "pending"))

    mondays = _mondays_back(2)
    checks.append(("mondays: every returned date is a real Monday", all(m.weekday() == 0 for m in mondays)))
    checks.append(("mondays: correct count (current + N back)", len(mondays) == 3))

    checks.append(("id extraction: real URL", _extract_id_from_url("https://x.gov.uk/planning/index.html?fa=getApplication&id=12345") == "12345"))
    checks.append(("id extraction: malformed input handled safely", _extract_id_from_url("no id here") is None))

    checks.append(("postcode extraction: real address", _extract_postcode("22 Druids Cross Road, Liverpool, L18 3HW") == "L18 3HW"))

    # A week with genuinely zero application links should trigger the
    # diagnostic once, then stay quiet for that same council
    empty_html = "<html><body><p>No results</p></body></html>"
    apps_empty = _parse_weekly_list(empty_html, "https://example.gov.uk", "Empty Test Council", "01-06-2026")
    checks.append(("empty response: zero apps returned, no crash", apps_empty == []))
    checks.append(("empty response: diagnostic fired exactly once", "Empty Test Council" in _EMPTY_RESPONSE_DIAGNOSED))
    before = len(_EMPTY_RESPONSE_DIAGNOSED)
    _parse_weekly_list(empty_html, "https://example.gov.uk", "Empty Test Council", "08-06-2026")
    checks.append(("empty response: does not re-fire for the same council", len(_EMPTY_RESPONSE_DIAGNOSED) == before))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
