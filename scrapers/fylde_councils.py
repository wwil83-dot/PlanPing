"""
PlanFind — Fylde Council config (2026-08-31).

Real, confirmed evidence: backlog_batch_recon3.py solved the
disclaimer-accept flow and enumerated all 16 real Advanced Search
fields. backlog_batch_recon4.py CONFIRMED a real date-range submission
returns real, structured results — 40 real Planning applications for
a 01/08/2026-30/08/2026 window.

REAL, CONFIRMED (not guessed):
  - Real disclaimer gate: /Disclaimer?returnUrl=... — a single "Agree"
    button click, session-based (cookie), only needs doing once per
    browser context/session.
  - Real search page: /Search/Advanced. Real date fields:
    DateReceivedFrom / DateReceivedTo (also DateDeterminedFrom/To,
    DateIssuedFrom/To, DateAppealFrom/To, DateAppealDecisionFrom/To —
    same platform family as Redcar and Cleveland Borough Council,
    near-identical field names).
  - Real submit control is a styled <button>, NOT a plain
    <input type="submit"> (unlike Redcar & Cleveland on the same
    platform family) — Playwright needed regardless since the form
    carries a genuine ASP.NET anti-forgery token.
  - Real results page: /Search/Results — returns THREE result types in
    one page (Planning/Appeals/Building Control tabs), each its OWN
    <table class="table-striped tblResults"> with an IDENTICAL column
    header (Application Number/Location/Proposal/Status) — the only
    reliable way to distinguish Planning from Building Control rows is
    the real detail-link URL prefix: /Planning/Display/<ref> vs
    /BuildingControl/Display/<ref>. PlanFind only wants Planning.
  - Real, CONFIRMED STABLE, plain-GET pagination:
    /Search/ResultsPage/<page>?module=PLA (module=BLD for Building
    Control) — 10 results per page. No session token in the URL
    itself, though the underlying session cookie (from the disclaimer
    accept) is still required — kept within the same Playwright
    browser context throughout, not re-fetched via a separate httpx
    client.
  - Real detail link: /Planning/Display/<reference> (e.g.
    /Planning/Display/26/0137) — genuinely a path segment containing a
    literal "/" from the reference itself.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Fylde Council": 536,
}

BASE_URL = "https://pa.fylde.gov.uk"
SEARCH_URL = f"{BASE_URL}/Search/Advanced"

INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Fylde Council','fylde-council','fylde_bespoke','england','https://pa.fylde.gov.uk/Search/Advanced','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'fylde_bespoke',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)
