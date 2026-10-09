"""
Shared Idox portals (2026-10-08).

PROBLEM: some Idox portals serve TWO councils from one list. idox_councils.py
has one entry per council pointing at the same URL, so each council saved the
portal's WHOLE list under its own name. Found by looking for the same reference
and address held under two councils: Cambridge/South Cambridgeshire (886 shared
rows), Maidstone/Swale (857), Bromsgrove/Redditch (755), Adur/Worthing (274),
Broadland/South Norfolk (245), and still being re-saved every night. Babergh's
portal also serves Mid Suffolk, which isn't scraped, so 283+ Mid Suffolk rows
sat under Babergh.

The Cambridge entry carries a local-authority filter (extra_search_param), but
that is only added to the page's address; nothing selects the dropdown before
the form is submitted, so it never took effect (the same trap as the month
dropdown).

FIX: decide each application's council from its POSTCODE. The scraper already
looks every postcode up on postcodes.io for its map position, and that service
also says which district the postcode is in. A council keeps only applications
whose postcode is in ITS district. Applications with no postcode cannot be
assigned and are NOT saved (counted in the log), rather than risk filing them
under the wrong council.

SHARED_PORTALS maps portal URL -> {postcodes.io "admin_district" name: the
council's name exactly as stored in the councils table}. If a district name ever
doesn't match, the first run's log shows "other council" for everything and
nothing is saved wrongly; fix the name here.
"""
import asyncio

import httpx

SHARED_PORTALS = {
    "https://pa.midkent.gov.uk/online-applications": {
        "Maidstone": "Maidstone Borough Council",
        "Swale": "Swale Borough Council",
    },
    "https://planning.adur-worthing.gov.uk/online-applications": {
        "Adur": "Adur District Council",
        "Worthing": "Worthing Borough Council",
    },
    "https://info.southnorfolkandbroadland.gov.uk/online-applications": {
        "Broadland": "Broadland District Council",
        "South Norfolk": "South Norfolk Council",
    },
    "https://publicaccess.bromsgroveandredditch.gov.uk/online-applications": {
        "Bromsgrove": "Bromsgrove District Council",
        "Redditch": "Redditch Borough Council",
    },
    "https://applications.greatercambridgeplanning.org/online-applications": {
        "Cambridge": "Cambridge City Council",
        "South Cambridgeshire": "South Cambridgeshire District Council",
    },
    "https://planning.baberghmidsuffolk.gov.uk/online-applications": {
        "Babergh": "Babergh District Council",
        "Mid Suffolk": "Mid Suffolk District Council",
    },
}

# Council rows that are NOT a real district: they duplicate a portal two real
# councils already cover. Used only by the cleanup script.
JOINT_COUNCILS = {
    "https://publicaccess.bromsgroveandredditch.gov.uk/online-applications": ["Bromsgrove and Redditch"],
}

# council name -> the postcodes.io district it owns
COUNCIL_DISTRICT = {council: district
                    for portal, mapping in SHARED_PORTALS.items()
                    for district, council in mapping.items()}


def norm_postcode(p) -> str:
    return (p or "").replace(" ", "").upper()


async def lookup_districts(postcodes, client=None) -> dict:
    """normalised postcode -> postcodes.io admin_district, or None if unknown."""
    unique = sorted({norm_postcode(p) for p in postcodes if p})
    out: dict = {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        for i in range(0, len(unique), 100):
            r = await client.post("https://api.postcodes.io/postcodes", json={"postcodes": unique[i:i + 100]})
            r.raise_for_status()
            for item in r.json().get("result", []):
                res = item.get("result")
                out[item["query"]] = res.get("admin_district") if res else None
            await asyncio.sleep(0.2)
    finally:
        if own:
            await client.aclose()
    return out


async def filter_to_council(apps: list, council_name: str, client=None):
    """Keep only applications whose postcode is in this council's own district.
    Returns (kept, stats). Raises if postcodes.io can't be reached: the caller
    must then save NOTHING, never the unfiltered list."""
    district = COUNCIL_DISTRICT[council_name]
    districts = await lookup_districts([a.get("postcode") for a in apps], client)
    kept, stats = [], {"kept": 0, "other_council": 0, "unresolved": 0}
    for a in apps:
        d = districts.get(norm_postcode(a.get("postcode")))
        if d is None:
            stats["unresolved"] += 1
        elif d == district:
            kept.append(a)
            stats["kept"] += 1
        else:
            stats["other_council"] += 1
    return kept, stats
