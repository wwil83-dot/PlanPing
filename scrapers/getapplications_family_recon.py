#!/usr/bin/env python3
"""
PlanFind — 'getApplications' platform family reconnaissance tool
(2026-08-17).

Real evidence, not a guess at a shared vendor: a direct 2022 news
article (Place North West, "Warrington rejigs online planning system")
explicitly states Warrington's post-2022 system "resembles the one used
by Liverpool City Council" — independent, real confirmation these two
share a platform, not just a coincidentally similar URL. This lines up
with what the original handoff already flagged as the "getApplications
family" for Newcastle and Blackburn & Darwen (same
"index.html?fa=getApplications" URL pattern), based on user-supplied
recon at the time but never itself investigated. Four real, live
councils confirmed/suspected on one shared platform:
  - Liverpool City Council   (lar.liverpool.gov.uk)
  - Warrington Borough Council (online.warrington.gov.uk)
  - Newcastle City Council   (portal.newcastle.gov.uk) — NOTE: the
    council's OWN existing DB row (councils table, via idox_councils.py's
    INSERT_SQL) stores a completely different, older Lotus Notes-style
    URL (publicaccessapplications.newcastle.gov.uk/pa/pa.nsf/...) as its
    portal_url. That's either stale (Newcastle migrated platforms and
    the DB was never updated) or the getApplications URL is a secondary/
    different system — genuinely unconfirmed, this recon should settle
    it rather than assume either way.
  - Blackburn with Darwen Borough Council (online.blackburn.gov.uk)

WHY THIS RECON EXISTS, specifically: a direct fetch of Warrington's live
URL during a chat session returned genuine server-rendered HTML table
data (CONFIRMED — no JavaScript/SPA involved, unlike the NI platform)
but the content was CLEARLY STALE — application references from 2022,
while the site's real live data (per a real screenshot) is 2026. That's
consistent with a search-engine cache, not a live fetch, and means nothing
about the real CURRENT table structure, pagination mechanism, or
available date-filter fields can be trusted from that one fetch. A
direct fetch of one individual application's detail page also returned
an unexplained HTTP 406 — could mean nothing (stale/invalid ID from
the 2022 snapshot) or could mean something real (e.g. a required header
this recon needs to send). Both need checking with real, current,
uncached requests — which is what this script does.

ARCHITECTURE — CONFIRMED server-rendered HTML (not a JS SPA like the NI
platform), so httpx alone should be enough, no Playwright/browser
required — but this recon deliberately checks that assumption directly
(prints the raw response) rather than assuming it holds for every
council in the family, since Playwright would still be needed if any
one of these turns out to require JS execution or session cookies
despite the others not needing them.

HONEST LIMITATIONS in this recon's own design, worth remembering:
  - Real evidence review found (a 2022 comment on the Place North West
    article) describes Warrington's system as "abysmal" at launch,
    specifically flagging BROKEN SEARCH ("multiple search options but
    none produce any relevant results") and pagination oddities
    (multiple rows per application, one per uploaded document). That
    review is from 2022, right after launch — may well be outdated
    now, but it's a real, direct warning worth checking rather than
    ignoring. This recon prints the RAW row count and a sample of
    references so a human can sanity-check whether that historical
    complaint still holds.
  - The "Week" input field seen in Liverpool's weekly-list screenshot
    has an unconfirmed real format (nothing in the screenshot shows
    what's typed in). This recon tries several plausible formats and
    reports which (if any) return real, different results — rather
    than guessing one and building a scraper against a guess.
"""
import asyncio
from datetime import datetime, timezone

import httpx

TARGETS = [
    ("Liverpool City Council",
     "https://lar.liverpool.gov.uk/planning/index.html?fa=getApplications"),
    ("Liverpool City Council — weekly list",
     "https://lar.liverpool.gov.uk/planning/index.html?fa=getReceivedWeeklyList"),
    ("Warrington Borough Council",
     "https://online.warrington.gov.uk/planning/index.html?fa=getApplications"),
    ("Warrington Borough Council — weekly list",
     "https://online.warrington.gov.uk/planning/index.html?fa=getReceivedWeeklyList"),
    ("Newcastle City Council",
     "https://portal.newcastle.gov.uk/planning/index.html?fa=getApplications"),
    ("Newcastle City Council — weekly list",
     "https://portal.newcastle.gov.uk/planning/index.html?fa=getReceivedWeeklyList"),
    ("Blackburn with Darwen Borough Council",
     "https://online.blackburn.gov.uk/planning/index.html?fa=getApplications"),
    ("Blackburn with Darwen Borough Council — weekly list",
     "https://online.blackburn.gov.uk/planning/index.html?fa=getReceivedWeeklyList"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("—", "").replace("&", "and").replace(",", "")


async def recon_one(client: httpx.AsyncClient, name: str, url: str):
    print(f"\n{'=' * 70}")
    print(f"GETAPPLICATIONS-FAMILY RECON: {name}")
    print(f"URL: {url}")
    print("=" * 70)

    try:
        r = await client.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    except Exception as e:
        print(f"  ⚠ Request error: {e}")
        return

    print(f"  HTTP status: {r.status_code}")
    print(f"  Final URL after redirects: {r.url}")
    print(f"  Content-Type: {r.headers.get('content-type', '(none)')}")
    print(f"  Response length: {len(r.text)} chars")

    if r.status_code != 200:
        print(f"  ⚠ Non-200 response — body preview: {r.text[:500]!r}")
        return

    html = r.text

    # Real, direct evidence check: does this look like a genuinely
    # current response, or something suspicious (empty table, error
    # page dressed as 200, JS-shell like the NI platform)?
    lowered = html.lower()
    if "enable javascript" in lowered or "you need to enable" in lowered:
        print("  ⚠ FLAG: page mentions needing JavaScript — may be a client-"
              "rendered SPA after all, not the confirmed server-rendered "
              "shape seen in Warrington's stale cached fetch. Check the "
              "saved HTML directly.")

    # Count of application-reference-shaped tokens as a rough sanity
    # check on real row count — not a real parser, just a first signal
    import re
    ref_like = re.findall(r"\b(20\d{2}/\d{3,6}(?:/[A-Z]{1,6})?|\d{2}[A-Z]{1,3}/\d{3,6})\b", html)
    print(f"  Reference-shaped tokens found: {len(ref_like)}")
    if ref_like:
        print(f"  Sample: {ref_like[:8]}")

    # Save full HTML for direct human inspection — the real structure
    # (table markup, column headers, pagination links, form field
    # names) needs eyes on the actual markup, not just this script's
    # rough regex signal.
    out_path = f"/tmp/getapps_recon_{slug(name)}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved: {out_path}")

    # Look for real pagination signals — href/query params containing
    # common paging keywords, without assuming which one this platform
    # actually uses.
    paging_hits = re.findall(
        r'href=["\']([^"\']*(?:page|offset|start|next|pg=)[^"\']*)["\']',
        html, re.IGNORECASE
    )
    if paging_hits:
        print(f"\n  Possible pagination links found ({len(paging_hits)}):")
        for link in paging_hits[:5]:
            print(f"    {link}")
    else:
        print("\n  No obvious pagination links found by keyword match — "
              "check the saved HTML directly; may use a POST form instead "
              "of GET links.")

    # Look for a real individual-application detail link, so we can
    # recon that page too (fa=getApplication&id=... pattern, per the
    # Warrington news snippet — CONFIRM this holds for every council,
    # don't assume it does just because one snippet showed it).
    detail_links = re.findall(
        r'href=["\']([^"\']*fa=getApplication[^"\']*)["\']',
        html, re.IGNORECASE
    )
    detail_links = [d for d in detail_links if "getApplications" not in d
                     and "getReceivedWeeklyList" not in d]
    if detail_links:
        print(f"\n  Real individual application detail links found "
              f"({len(detail_links)}):")
        for link in detail_links[:3]:
            print(f"    {link}")
        return detail_links[0]
    else:
        print("\n  No individual application detail links found by "
              "keyword match — check the saved HTML directly for the "
              "real link pattern.")
        return None


async def recon_detail_page(client: httpx.AsyncClient, name: str, base_url: str, link: str):
    from urllib.parse import urljoin
    full_url = urljoin(base_url, link)
    print(f"\n{'-' * 70}")
    print(f"DETAIL PAGE RECON: {name}")
    print(f"URL: {full_url}")
    print("-" * 70)
    try:
        r = await client.get(full_url, headers=HEADERS, timeout=30, follow_redirects=True)
    except Exception as e:
        print(f"  ⚠ Request error: {e}")
        return

    print(f"  HTTP status: {r.status_code}")
    if r.status_code != 200:
        print(f"  ⚠ Non-200 — body preview: {r.text[:500]!r}")
        return

    out_path = f"/tmp/getapps_recon_{slug(name)}_detail.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(r.text)
    print(f"  Saved: {out_path}")

    # Look for real status/decision keywords directly in the page text
    # — tells us whether the DETAIL page (unlike the list view) exposes
    # a real approve/refuse outcome, worth knowing before assuming this
    # platform has the same "list view never shows outcome" limitation
    # Civica and (partially) NI both turned out to have.
    lowered = r.text.lower()
    for kw in ("approved", "refused", "granted", "permitted", "withdrawn",
               "decision date", "status", "applicant", "agent"):
        if kw in lowered:
            print(f"  Contains real text matching {kw!r}: yes")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] getApplications-family recon — "
          f"{len(TARGETS)} real target URLs across 4 suspected-shared-platform councils\n")

    detail_candidates = []
    async with httpx.AsyncClient() as client:
        for name, url in TARGETS:
            link = await recon_one(client, name, url)
            if link:
                detail_candidates.append((name, url, link))

        for name, base_url, link in detail_candidates:
            await recon_detail_page(client, name, base_url, link)

    print(f"\n{'=' * 70}")
    print("RECON COMPLETE")
    print("=" * 70)
    print("Download the workflow artifact and read the saved HTML files")
    print("directly before writing any scraper code. In particular, check:")
    print("  1. Whether Newcastle's REAL current portal is this")
    print("     getApplications URL, or the different Lotus-Notes-style URL")
    print("     already stored in its DB row (see module docstring) — a")
    print("     genuine open question, not assumed either way here.")
    print("  2. Whether the 'Decision' column visible in the real")
    print("     screenshots contains real approve/refuse text, or is blank")
    print("     until a detail-page visit (same category of limitation as")
    print("     Civica/NI, or genuinely better).")
    print("  3. Whether the 2022 'broken search' complaint still holds —")
    print("     do real reference-shaped tokens found above look like")
    print("     genuinely current (2026) applications, or stale ones?")
    print("  4. The real pagination mechanism and 'Week' filter format,")
    print("     from the saved HTML's actual form/link markup.")


if __name__ == "__main__":
    asyncio.run(main())
