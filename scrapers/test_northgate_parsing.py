#!/usr/bin/env python3
"""Test for northgate_scraper.py's table-parsing logic (2026-07-24) —
using real HTML pulled directly from the Runnymede results-page recon."""
from northgate_scraper import _parse_results_table, _normalise_status, _extract_postcode, _parse_date

# Real HTML structure, pulled directly from the actual recon artifact —
# trimmed to the relevant table but otherwise verbatim, including the
# real embedded newlines in the address field.
REAL_TABLE_HTML = """
<table cellspacing="2" cellpadding="4" summary="Results of the Search" class="display_table">
  <tbody><tr class="Row0">
    <th class="data_header">Application Number</th>
    <th class="data_header">Site Address</th>
    <th class="data_header">Development Description</th>
    <th class="data_header">Status</th>
    <th class="data_header">Date Registered</th>
    <th class="data_header">Decision</th>
  </tr>
  <tr class="Row1">
    <td title="View Application Details" class="TableData">
      <a class="data_text" href="StdDetails.aspx?PT=Planning Applications On-Line&amp;TYPE=PL/PlanningPK.xml&amp;PARAM0=
					383460&amp;XSLT=
					/Northgate/PlanningExplorer/SiteFiles/Skins/Runnymede_AA/xslt/PL/PLDetails.xslt&amp;FT=Planning Application Details&amp;PUBLIC=
					Y&amp;XMLSIDE=/Northgate/PlanningExplorer/SiteFiles/Skins/Runnymede_AA/Menus/PL.xml&amp;DAURI=PLANNING
					">RU.26/0984</a>
    </td>
    <td class="data_text" title="Site Address">TPO 473 at Squires Garden Centre
Holloway Hill
Lyne
Chertsey
Surrey
KT16 0AE</td>
    <td class="data_text" title="Development Description">5 day notice</td>
    <td class="data_text" title="Status">REGISTERED</td>
    <td class="data_text" title="Date Registered">24-07-2026</td>
    <td class="data_text" title="Decision"></td>
  </tr>
  <tr class="Row0">
    <td title="View Application Details" class="TableData">
      <a class="data_text" href="StdDetails.aspx?PT=Planning Applications On-Line&amp;TYPE=PL/PlanningPK.xml&amp;PARAM0=383400">RU.26/0977</a>
    </td>
    <td class="data_text" title="Site Address">Fairmont Hotel,
Fairmont Windsor Park,
Bishopsgate Road
Egham
TW20 0YL</td>
    <td class="data_text" title="Development Description">5 day notice</td>
    <td class="data_text" title="Status">FINAL DECISION</td>
    <td class="data_text" title="Date Registered">23-07-2026</td>
    <td class="data_text" title="Decision">Approve</td>
  </tr>
  </tbody>
</table>
"""


# Real page-2 HTML structure, confirmed via direct comparison against
# page 1 — genuinely omits XMLSIDE's value (Runnymede's own server
# behavior, not our bug), while XSLT remains present and correct.
REAL_PAGE2_HTML = """
<table cellspacing="2" cellpadding="4" summary="Results of the Search" class="display_table">
  <tbody><tr class="Row0">
    <th class="data_header">Application Number</th>
    <th class="data_header">Site Address</th>
    <th class="data_header">Development Description</th>
    <th class="data_header">Status</th>
    <th class="data_header">Date Registered</th>
    <th class="data_header">Decision</th>
  </tr>
  <tr class="Row1">
    <td title="View Application Details" class="TableData">
      <a class="data_text" href="StdDetails.aspx?PT=Planning Applications On-Line&amp;TYPE=PL/PlanningPK.xml&amp;PARAM0=
					382992&amp;XSLT=
					/Northgate/PlanningExplorer/SiteFiles/Skins/Runnymede_AA/xslt/PL/PLDetails.xslt&amp;FT=Planning Application Details&amp;PUBLIC=
					Y&amp;XMLSIDE=&amp;DAURI=PLANNING
					">RU.26/0930</a>
    </td>
    <td class="data_text" title="Site Address">1 Example Road</td>
    <td class="data_text" title="Development Description">Test proposal</td>
    <td class="data_text" title="Status">REGISTERED</td>
    <td class="data_text" title="Date Registered">10-07-2026</td>
    <td class="data_text" title="Decision"></td>
  </tr>
  </tbody>
</table>
"""


def run():
    checks = []

    apps = _parse_results_table(
        REAL_TABLE_HTML,
        "https://planning.runnymede.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
        "Runnymede Borough Council",
    )

    checks.append(("parses exactly 2 real applications", len(apps) == 2))

    if len(apps) == 2:
        pending_app, decided_app = apps[0], apps[1]

        checks.append(("real reference extracted correctly", pending_app["reference"] == "RU.26/0984"))
        checks.append(("real address normalised (newlines collapsed)",
                        pending_app["address"] == "TPO 473 at Squires Garden Centre Holloway Hill Lyne Chertsey Surrey KT16 0AE"))
        checks.append(("real postcode extracted from address", pending_app["postcode"] == "KT16 0AE"))
        checks.append(("empty Decision + status REGISTERED -> pending", pending_app["status"] == "pending"))
        checks.append(("no decision_date when Decision is empty", pending_app["decision_date"] is None))
        checks.append(("real detail URL constructed correctly",
                        "StdDetails.aspx" in pending_app["council_url"] and "PARAM0=383460" in pending_app["council_url"]))

        # Real bug found via a user report (2026-07-24): the raw href
        # has literal embedded newlines/tabs as HTML source line-wrap
        # artifacts (reproduced above, verbatim from the real page),
        # which a real browser strips automatically but raw extraction
        # doesn't — producing a broken URL that 404s when actually
        # opened. Must be fixed WITHOUT also stripping genuine spaces
        # within real values (confirmed working in the actual browser
        # URL, encoded as %20, not removed).
        checks.append(("PARAM0 value has no embedded newline/tab corruption",
                        "\n" not in pending_app["council_url"] and "\t" not in pending_app["council_url"]))
        checks.append(("genuine spaces within real values are preserved (not over-stripped)",
                        "Planning Applications On-Line" in pending_app["council_url"]))

        checks.append(("real 'Approve' decision -> approved status", decided_app["status"] == "approved"))
        checks.append(("decision_date set when Decision is present", decided_app["decision_date"] == "2026-07-23"))
        checks.append(("real submitted_date parsed correctly", decided_app["submitted_date"] == "2026-07-23"))

    # Status mapping edge cases
    checks.append(("refuse-flavoured decision maps to refused",
                    _normalise_status("FINAL DECISION", "Refuse", "test") == "refused"))
    checks.append(("withdraw-flavoured decision maps to withdrawn",
                    _normalise_status("FINAL DECISION", "Withdrawn", "test") == "withdrawn"))
    checks.append(("date-only parsing handles dd-mm-yyyy (Northgate's real format)",
                    _parse_date("24-07-2026") == "2026-07-24"))

    # Real bug found via a user report (2026-07-24/25): page 2+ of a
    # search genuinely omits XMLSIDE's value on Runnymede's OWN server —
    # confirmed via direct comparison of real page 1 vs page 2 HTML.
    # Deriving the missing value from XSLT (reliably present on every
    # page) rather than hardcoding Runnymede's specific skin folder name.
    page2_apps = _parse_results_table(
        REAL_PAGE2_HTML,
        "https://planning.runnymede.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
        "Runnymede Borough Council",
    )
    checks.append(("page 2 parses successfully despite missing source XMLSIDE", len(page2_apps) == 1))
    if page2_apps:
        checks.append(("derived XMLSIDE value is correct, not left empty",
                        "XMLSIDE=/Northgate/PlanningExplorer/SiteFiles/Skins/Runnymede_AA/Menus/PL.xml"
                        in page2_apps[0]["council_url"]))
        checks.append(("derived URL has no dangling empty XMLSIDE=&",
                        "XMLSIDE=&" not in page2_apps[0]["council_url"]))

    all_ok = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
