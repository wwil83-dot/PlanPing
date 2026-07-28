#!/usr/bin/env python3
"""
PlanFind — Idox gap-list batch prober (round 2, 2026-07-28).

PURPOSE: round 1 tested 121 candidates against 3 URL conventions and
found 7 hits, but only 3 (Brentwood, Cheltenham, Lewisham) turned out
to be genuinely real once verified with a full recon pass — the other
4 were false positives (a stale test subdomain, a redirect to the
council's general homepage, an explicit 404). Round 1's resolved
councils (Brentwood/Cheltenham/Lewisham/Chesterfield added, Bristol/
Camden already covered via data_gov_uk) are removed from this round's
candidate list; Redbridge is being checked separately with its
already-known real URL rather than re-guessed here. This round tests
4 MORE real, confirmed-elsewhere URL conventions (not guessed blind —
seen working for actual councils already added this session) against
the remaining 114 candidates, on top of the original 3.

CONVENTIONS TESTED (derived from the naming pattern of our existing 205
confirmed-working Idox councils, plus round-2 additions):
  1. https://publicaccess.{slug}.gov.uk/online-applications/
  2. https://planning.{slug}.gov.uk/online-applications/
  3. https://{slug}.gov.uk/online-applications/
  4. https://www.{slug}.gov.uk/online-applications/ (Redbridge, Ipswich,
     Waltham Forest's real convention)
  5. https://pa.{slug}.gov.uk/online-applications/ (Brent's real
     convention)
  6. https://publicaccess.{hyphenated-slug}.gov.uk/online-applications/
  7. https://planning.{hyphenated-slug}.gov.uk/online-applications/
     (North Somerset's real convention uses a hyphenated slug —
     n-somerset, not northsomerset — worth testing for other
     multi-word council names too)

A "hit" is a response containing recognisable Idox markers (the exact
page title/text patterns confirmed across many real Idox councils this
session: "Idox", "Online Applications", "search.do", or the presence of
a genuine month/date-search form). This is a FIRST-PASS FILTER, not a
final confirmation — same discipline as everything else this session:
treat hits as candidates needing a real recon pass (idox_multi_recon.py-
style) before being added to idox_councils.py, not as confirmed
automatically. False positives (a URL that resolves but isn't actually
Idox) and false negatives (a real Idox council using an unusual
subdomain convention neither tested here) are both expected — this
narrows 122 candidates down to a manageable shortlist, it doesn't
replace verification.
"""
import asyncio
import re

import httpx

# Real gap-list candidates — England/Wales/Scotland councils with no
# confirmed vendor, excluding National Parks/Development Corporations
# (lower volume, deprioritized) and St Albans (already resolved via
# Civica).
CANDIDATES = [
    "Amber Valley", "Arun", "Ashfield", "Barking and Dagenham", "Barnsley",
    "Bath and North East Somerset", "Birmingham", "Blackburn with Darwen",
    "Boston", "Bournemouth Christchurch and Poole",
    "Broxbourne", "Burnley", "Cannock Chase",
    "Central Bedfordshire", "Charnwood", "Cherwell",
    "Colchester", "County Durham", "Coventry", "Crawley",
    "Dacorum", "Doncaster", "Dorset", "Dudley", "East Hampshire",
    "East Riding of Yorkshire", "East Staffordshire", "Eastbourne",
    "Fareham", "Fenland", "Fylde", "Gedling", "Great Yarmouth", "Greenwich",
    "Hackney", "Harrow", "Hartlepool", "Hastings", "Havering",
    "Herefordshire County of", "High Peak", "Hillingdon", "Hounslow",
    "Hyndburn", "Isles of Scilly", "Islington", "Kensington and Chelsea",
    "Kingston upon Hull City of", "Kirklees", "Lancaster",
    "Lichfield", "Malvern Hills", "Melton", "Merton", "Mid Suffolk",
    "Mole Valley", "Newcastle upon Tyne", "Newcastle-under-Lyme",
    "North Devon", "North Kesteven", "North Lincolnshire",
    "North Warwickshire", "Nuneaton and Bedworth", "Oadby and Wigston",
    "Oldham", "Plymouth", "Preston", "Redcar and Cleveland",
    "Ribble Valley", "Rochford", "Rotherham", "Rugby", "Slough",
    "South Derbyshire", "South Hams", "South Holland", "South Kesteven",
    "South Oxfordshire", "South Tyneside", "St. Helens",
    "Staffordshire Moorlands", "Stratford-on-Avon", "Swindon", "Tamworth",
    "Tandridge", "Telford and Wrekin", "Torbay", "Tower Hamlets",
    "Vale of White Horse", "Walsall", "Wandsworth", "Watford", "West Devon",
    "West Lindsey", "West Northamptonshire", "Westmorland and Furness",
    "Wokingham", "Worcester", "Wychavon",
    # Wales
    "Blaenau Gwent", "Bridgend", "Ceredigion", "Conwy", "Flintshire",
    "Gwynedd", "Merthyr Tydfil", "Pembrokeshire", "Rhondda Cynon Taf",
    "Vale of Glamorgan",
    # Scotland
    "Aberdeenshire", "Dumfries and Galloway", "East Ayrshire",
    "Na h-Eileanan Siar", "West Dunbartonshire",
]

IDOX_MARKERS = [
    "idox", "online-applications", "search.do", "PublicAccess",
    "monthly list", "weekly list", "planning applications",
]


def slugify(name: str) -> str:
    """Best-effort slug matching the dominant convention seen across our
    205 confirmed Idox councils — lowercase, no spaces/punctuation.
    Strips ONS-standard suffix patterns (e.g. "Bristol City of" -> just
    "bristol") that would never appear in a real subdomain."""
    s = name
    s = re.sub(r"\s+(City|County|Borough)\s+of$", "", s, flags=re.IGNORECASE)
    s = s.lower()
    s = re.sub(r"[^a-z]", "", s)
    return s


def slugify_hyphenated(name: str) -> str:
    """Real, confirmed variant seen this session — some councils use a
    hyphenated slug rather than a plain concatenation (e.g. North
    Somerset's real URL is planning.n-somerset.gov.uk, not
    northsomerset). Keeps hyphens between words, still strips the same
    ONS suffix patterns."""
    s = name
    s = re.sub(r"\s+(City|County|Borough)\s+of$", "", s, flags=re.IGNORECASE)
    s = s.lower()
    s = re.sub(r"[^a-z\s]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s


async def probe_one(client: httpx.AsyncClient, name: str) -> dict:
    slug = slugify(name)
    hyphen_slug = slugify_hyphenated(name)
    patterns = [
        f"https://publicaccess.{slug}.gov.uk/online-applications/",
        f"https://planning.{slug}.gov.uk/online-applications/",
        f"https://{slug}.gov.uk/online-applications/",
        # Added round 2 (2026-07-28) — real, confirmed conventions from
        # councils already added this session, not guessed blind:
        # www. (Redbridge, Ipswich, Waltham Forest), pa. (Brent),
        # hyphenated slug (North Somerset's real URL is
        # planning.n-somerset.gov.uk, not northsomerset).
        f"https://www.{slug}.gov.uk/online-applications/",
        f"https://pa.{slug}.gov.uk/online-applications/",
        f"https://publicaccess.{hyphen_slug}.gov.uk/online-applications/",
        f"https://planning.{hyphen_slug}.gov.uk/online-applications/",
    ]

    for url in patterns:
        try:
            r = await client.get(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                body_lower = r.text.lower()
                marker_hits = [m for m in IDOX_MARKERS if m.lower() in body_lower]
                if marker_hits:
                    return {
                        "name": name, "url": url, "status": "HIT",
                        "markers": marker_hits, "http_status": r.status_code,
                    }
        except Exception:
            continue

    return {"name": name, "url": None, "status": "MISS", "markers": [], "http_status": None}


async def main():
    print(f"Probing {len(CANDIDATES)} candidates against common Idox URL "
          f"conventions...\n")

    hits = []
    misses = []

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 PlanFind-recon/1.0"}) as client:
        # Concurrency-limited batch, not fully sequential — lightweight
        # HTTP checks, not full browser automation, so this is cheap
        sem = asyncio.Semaphore(8)

        async def bounded_probe(name):
            async with sem:
                return await probe_one(client, name)

        results = await asyncio.gather(*[bounded_probe(name) for name in CANDIDATES])

    for r in results:
        if r["status"] == "HIT":
            hits.append(r)
            print(f"  ✓ HIT: {r['name']} -> {r['url']} (markers: {', '.join(r['markers'][:3])})")
        else:
            misses.append(r)

    print(f"\n{'=' * 60}")
    print(f"HITS: {len(hits)}  /  MISSES: {len(misses)}  /  Total: {len(CANDIDATES)}")
    print("\nHit list (candidates worth a real recon pass before adding):")
    for r in hits:
        print(f"  (\"{r['name']}\",\n   \"{r['url'].rstrip('/')}\"),")

    print(f"\nMissed (no hit on any of the 3 tested conventions — genuinely "
          f"unknown vendor, or uses a different subdomain pattern):")
    print(", ".join(r["name"] for r in misses))


if __name__ == "__main__":
    asyncio.run(main())
