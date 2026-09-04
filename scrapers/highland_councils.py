"""
PlanFind — Highland Council config (2026-09-04).

Real, confirmed evidence: found via the user's own browsing as an
alternate route around Highland's own (assumed, not individually
re-tested) blocked Idox instance — the same pattern as several other
councils this session (Gloucester, Edinburgh) — highland.gov.uk's own
weekly-list PDF page, a URL shape identical to Edinburgh's own weekly-
list page, likely the same shared council-website CMS platform used
across multiple Scottish councils.

REAL, CONFIRMED (not guessed):
  - Real weekly-list page: highland.gov.uk/downloads/download/1137/
    weekly-list-of-planning-applications — genuinely NOT behind any
    Cloudflare/WAF challenge at all (unlike Glasgow's equivalent PDF
    page, which was genuinely blocked after 3 different real attempts
    and ultimately parked). Real, confirmed 24 weeks of history
    available directly on this one page.
  - Real PDF file links trigger a genuine forced download
    (Content-Disposition: attachment) rather than rendering in-browser
    — needs Playwright's expect_download() API, not a plain page
    navigation or HTTP client.
  - Real PDF structure: NOT a genuine table (pdfplumber's table
    extraction produces unreliable, fragmented 2-4-row junk tables from
    incidental whitespace) — the real, reliable structure is in the
    PLAIN EXTRACTED TEXT, a consistent repeating pattern per
    application:
      "Ref Number <ref> Application Type <type>"
      "Validation Date <date> Grid Reference <easting> <northing>"
      "Expiry Date for lodging Representations <date>"
      "Description of Works <text, may span multiple lines>"
      "Location of Works <site address, may span multiple lines>"
      "Community Council <name>"
      "Applicant Name <name>"
      "Applicant Address <address, may span multiple lines>"
      "Case Officer <name>" (optionally followed by phone/email lines)
      "The following application was submitted online" (a fixed
      trailer sentence marking the end of one record, not always
      present)
  - Real, confirmed: many rural "Location of Works" values have NO
    postcode at all (e.g. "Land 215M SE Of 62, Tarbet, Scourie, ,") —
    expect a meaningfully higher council-centroid geocoding fallback
    rate than most other platforms in this project, an honest
    reflection of genuinely rural/agricultural/utility applications,
    not a parsing failure.
  - HONEST LIMITATION: this is a RECEIVED-applications weekly list only
    — no decision/status field appears anywhere in the real PDF
    structure. All applications are filed as 'pending'; this feed
    cannot tell us about approvals/refusals at all. Matches several
    other "weekly list of received applications" platforms already
    handled this way elsewhere in this project.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Highland Council": 332,
}

WEEKLY_LIST_URL = "https://www.highland.gov.uk/downloads/download/1137/weekly-list-of-planning-applications"
BASE_URL = "https://www.highland.gov.uk"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Highland Council','highland-council','highland_pdf','scotland','https://www.highland.gov.uk/downloads/download/1137/weekly-list-of-planning-applications','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'highland_pdf',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
