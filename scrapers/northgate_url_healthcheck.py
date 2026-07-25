#!/usr/bin/env python3
"""
PlanFind — Northgate stored-URL health check (2026-07-25).

Built after finding that the XMLSIDE-derivation fix in northgate_scraper.py
works for MOST but not ALL records (9 of 10 spot-checked) — rather than
keep patching one broken record at a time from manual spot-checks, this
actually REQUESTS every currently-stored Northgate council_url and
reports which ones genuinely succeed or fail, giving the true picture in
one pass.

Does NOT touch Supabase writes — read-only, purely diagnostic. Prints a
summary plus the raw response text for any failures, so the next fix (if
needed) is based on real evidence of what's actually different about the
remaining broken ones, not another guess.
"""
import asyncio
import os
import sys

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }


async def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)

    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/planning_applications",
            params={
                "select": "reference,council_url",
                "council_id": "eq.404",
                "order": "submitted_date.desc",
                "limit": "100",
            },
            headers=_h(),
        )
        r.raise_for_status()
        rows = r.json()

    print(f"Checking {len(rows)} stored Runnymede URLs…\n")

    ok_count = 0
    fail_count = 0
    failures = []

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        for row in rows:
            ref = row["reference"]
            url = row["council_url"]
            try:
                resp = await c.get(url)
                body = resp.text
                is_error = (
                    "Server Error" in body
                    or "resource cannot be found" in body
                    or "HTTP 404" in body
                    or resp.status_code >= 400
                )
                if is_error:
                    fail_count += 1
                    failures.append((ref, url, resp.status_code))
                    print(f"  ✗ {ref}: FAILED (HTTP {resp.status_code})")
                else:
                    ok_count += 1
                    print(f"  ✓ {ref}: OK")
            except Exception as e:
                fail_count += 1
                failures.append((ref, url, f"exception: {e}"))
                print(f"  ✗ {ref}: EXCEPTION — {e}")

            await asyncio.sleep(0.3)  # be polite to Runnymede's server

    print(f"\n{'=' * 50}")
    print(f"OK: {ok_count}  /  FAILED: {fail_count}  /  Total: {len(rows)}")

    if failures:
        print("\nFailed URLs (for direct comparison against working ones):")
        for ref, url, status in failures:
            print(f"\n  {ref} ({status}):")
            print(f"  {url}")

    # SESSION-DEPENDENCY TEST (2026-07-25): every stored URL failed above,
    # including ones with a fully correct, cleanly-populated XMLSIDE —
    # ruling out data corruption as the explanation. Real hypothesis:
    # these detail pages might not be genuine standalone permalinks at
    # all, only resolving within an active session established by first
    # loading/submitting the real search form (cookies along the way),
    # not a bare cold request. Testing directly rather than guessing
    # further — visit the real search page first (establishing whatever
    # session state a real browser would), THEN try a detail URL with
    # that same session, compared against the cold request already
    # confirmed failing above.
    if failures:
        print(f"\n{'=' * 50}")
        print("SESSION-DEPENDENCY TEST")
        test_ref, test_url, _ = failures[0]
        print(f"Testing {test_ref} — with a session established first vs cold\n")

        search_url = "https://planning.runnymede.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx"
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            try:
                search_resp = await c.get(search_url)
                print(f"Search page loaded: HTTP {search_resp.status_code}, "
                      f"cookies received: {list(c.cookies.keys())}")
            except Exception as e:
                print(f"⚠ Couldn't load search page: {e}")
                return

            await asyncio.sleep(1)

            try:
                detail_resp = await c.get(test_url)
                body = detail_resp.text
                is_error = (
                    "Server Error" in body
                    or "resource cannot be found" in body
                    or detail_resp.status_code >= 400
                )
                print(f"\nDetail page with session cookies: HTTP {detail_resp.status_code}")
                print(f"Still shows error: {is_error}")
                if not is_error:
                    print("\n✓ SESSION-DEPENDENCY CONFIRMED — the same URL that failed cold "
                          "succeeds once a session is established first. This means these "
                          "detail links are NOT standalone permalinks — storing them for "
                          "later use won't work regardless of how clean the URL is.")
                else:
                    print("\n✗ Still fails even with a session — session-dependency "
                          "theory REFUTED, the real cause is something else entirely.")
            except Exception as e:
                print(f"⚠ Detail request with session failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
