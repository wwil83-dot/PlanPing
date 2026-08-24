"""
PlanFind — Barrow (Westmorland and Furness Council) config (2026-08-24).

Real, confirmed evidence backing every design decision here — full
recon trail: wandf_recon.py, wandf_recon_round2.py, wandf_recon_round3.py,
barrow_iframe_check.py.

REAL, CONFIRMED (not guessed):
  - Genuinely separate system from Eden/South Lakeland (see
    esl_councils.py) — Barrow uses "Barrow Planning Hub", built on
    Oracle APEX. Same real council entity (Westmorland and Furness),
    deliberately using the SAME real single official council name so
    this scraper's data lands on the SAME council_id as esl_scraper.py's
    Eden/South Lakeland data, not a second, confusing entry.
  - Real, confirmed 3-level structure:
      1. Weekly List overview (base URL below) — a real table, "Week
         Commencing | Validated | Decided", each cell a real button
         with a data-ajax-target-style javascript:apex.navigation.
         dialog(...) call, real counts shown as "View (N)".
      2. Clicking a real "View" link opens a genuinely SEPARATE
         iframe (confirmed via barrow_iframe_check.py — NOT inline DOM
         injection) with its own real URL
         (f?p=BARROWPLANNINGHUB:VALIDATEDLIST:{session}::NO:RP,1011:
         P1011_WEEK_COMMENCING:{date}&cs=...&p_dialog_cs=...). Real,
         confirmed table inside: "View | Reference number | Location |
         Proposal | Validated date".
      3. Each row's own "View" button opens a THIRD, further-nested
         real dialog (APPLICATIONDETAILS) — never actually recon'd in
         detail. Its real URL carries session-bound security tokens
         (cs=/p_dialog_cs=), NOT a stable/reusable id-only URL like
         Hartlepool's /Planning/Display/{ref} — meaning a pending-
         recheck mechanism may not be buildable the same way as every
         other platform here. Deliberately NOT attempted in v1 — see
         module docstring's honest limitation.
  - V1 scope deliberately covers ONLY the real "Validated" list (newly
    submitted applications) — matches this project's core purpose
    everywhere else. The real "Decided" list's own column structure
    was never directly recon'd; decision status is a genuine, honest
    gap for now.
"""

COUNCIL_DB_IDS: dict[str, int | None] = {
    "Westmorland and Furness Council": 513,
}

BASE_URL = "https://webapps.barrowbc.gov.uk/webapps/f?p=BARROWPLANNINGHUB:WEEKLYLIST:10007760192139::NO:::"

# Barrow doesn't need its own INSERT_SQL — it deliberately shares the
# SAME real council row already created for Eden/South Lakeland (see
# esl_councils.py's own INSERT_SQL). Nothing to insert here.

if __name__ == "__main__":
    print(f"Barrow shares council_id={COUNCIL_DB_IDS['Westmorland and Furness Council']} "
          f"with esl_scraper.py's Eden/South Lakeland data — no separate "
          f"INSERT_SQL needed.")
