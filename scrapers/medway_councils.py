"""
PlanFind — Medway Council (Open Digital Planning) config (2026-08-28).

Real, confirmed evidence: oneoff_batch_recon.py, medway_search_recon.py.

REAL, CONFIRMED (not guessed):
  - This is a shared, multi-council Open Digital Planning platform
    (planningregister.org) — Medway is just one council slug among
    several, confirmed via a real hidden council=medway form
    parameter, same category as OcellaWeb.
  - Real, HONEST, OFFICIAL caveat directly from the site's own text:
    "Not all planning applications are available on this register." —
    this is explicitly, officially confirmed to be an incomplete pilot
    covering ONLY a subset of Medway's real applications. The old
    Idox URL (publicaccess.medway.gov.uk) is confirmed genuinely dead
    (DNS/timeout failure) — this pilot register may be the only real,
    live source currently available, incomplete or not.
  - Genuinely different architecture from every other platform in this
    project: NO real date-range search exists at all — just a keyword
    search and a real, recency-sorted "Recently published
    applications" list with simple page-number pagination
    (?page=N&resultsPerPage=10&type=simple). An empty keyword search
    correctly returns this same full listing.
  - Real, clean, semantic structure: article.dpr-application-card,
    each real field a <dl><dt>label</dt><dd>value</dd></dl> pair —
    Application reference, Address, Description, Application type,
    Status, Received date, Valid from date, Published date,
    Consultation end date.
  - Real, confirmed permanent detail link via "View details of
    {reference}" — a genuine, safe pending-recheck mechanism is
    possible here.
  - No real total-count text confirmed — using the real presence/
    absence of a "Next page" link as the pagination stop condition,
    combined with a real date-based early-exit once a page's own
    "Received date" values fall outside the desired window (since
    there's no way to directly filter by date, only page through a
    recency-sorted list).
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Medway Council": None,
}

BASE_URL = "https://planningregister.org/medway"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Medway Council','medway-council','medway_odp','england','https://planningregister.org/medway','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'medway_odp',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
