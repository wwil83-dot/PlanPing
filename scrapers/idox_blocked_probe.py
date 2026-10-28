"""Idox blocked / pending councils: can the plain-HTTP scraper (curl_cffi advanced search) reach them? Writes nothing.

For each of 66 Idox councils that have no scraper running (coverage_source 'manual_link' or 'pending'):
  1. GET the advanced search form with a plain httpx client (baseline) and with curl_cffi (Chrome fingerprint)
  2. if curl_cffi gets the real form: POST a small date-received search (last 10 days) and count the result cards
Verdicts:  OK_HTTP (form + search works, add to idox_http_scraper) | FORM_ONLY (form loads, search does not) |
           CHALLENGE (Cloudflare / WAF) | RECAPTCHA_FORM | MOVED (redirected to another host) | TIMEOUT | ERROR | NOT_IDOX
"""
import asyncio
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx
from curl_cffi.requests import AsyncSession

COUNCILS = [
    (296, 'Gosport Borough Council', 'https://publicaccess.gosport.gov.uk/online-applications', 'manual_link'),
    (0, 'Melton Borough Council', 'https://pa.melton.gov.uk/online-applications', 'manual_link'),
    (1, 'Aberdeen City Council', 'https://publicaccess.aberdeencity.gov.uk/online-applications', 'manual_link'),
    (392, 'Bassetlaw District Council', 'https://publicaccess.bassetlaw.gov.uk/online-applications', 'manual_link'),
    (282, 'Braintree District Council', 'https://publicaccess.braintree.gov.uk/online-applications', 'manual_link'),
    (303, 'Breckland District Council', 'https://planning.breckland.gov.uk/online-applications', 'pending'),
    (20, 'Brighton and Hove City Council', 'https://planningapps.brighton-hove.gov.uk/online-applications', 'manual_link'),
    (170, 'Bury Metropolitan Borough Council', 'https://planning.bury.gov.uk/online-applications', 'pending'),
    (176, 'Calderdale Metropolitan Borough Council', 'https://portal.calderdale.gov.uk/online-applications', 'manual_link'),
    (416, 'Cambridgeshire County Council', 'https://planning.cambridgeshire.gov.uk/online-applications', 'pending'),
    (26, 'Cardiff Council', 'https://www.cardiffidoxcloud.wales/publicaccess', 'manual_link'),
    (322, 'Carmarthenshire County Council', 'https://planning.carmarthenshire.gov.uk/online-applications', 'manual_link'),
    (267, 'City of Westminster', 'https://idoxpa.westminster.gov.uk/online-applications', 'manual_link'),
    (342, 'Clackmannanshire Council', 'https://publicaccess.clacks.gov.uk/publicaccess', 'manual_link'),
    (279, 'Crawley Borough Council', 'https://planningregister.crawley.gov.uk/online-applications', 'pending'),
    (32, 'Derby City Council', 'https://eplanning.derby.gov.uk/online-applications', 'manual_link'),
    (355, 'Dumfries and Galloway Council', 'https://eaccess.dumgal.gov.uk/online-applications', 'pending'),
    (295, 'East Hampshire District Council', 'https://planningpublicaccess.easthants.gov.uk/online-applications', 'pending'),
    (324, 'Eryri National Park Authority', 'https://pa.eryri.llyw.cymru/publicaccess', 'pending'),
    (37, 'Gloucester City Council', 'https://planning.plymouth.gov.uk/online-applications', 'pending'),
    (360, 'Harborough District Council', 'https://pa2.harborough.gov.uk/online-applications', 'manual_link'),
    (285, 'Harlow District Council', 'https://planningonline.harlow.gov.uk/online-applications', 'manual_link'),
    (297, 'Hart District Council', 'https://publicaccess.hart.gov.uk/online-applications', 'manual_link'),
    (308, 'Lancaster City Council', 'https://planning.lancaster.gov.uk/online-applications', 'pending'),
    (49, 'Leicester City Council', 'https://publicaccess.leicester.gov.uk/online-applications', 'pending'),
    (228, 'London Borough of Bexley', 'https://pa.bexley.gov.uk/online-applications', 'manual_link'),
    (242, 'London Borough of Enfield', 'https://planningandbuildingcontrol.enfield.gov.uk/online-applications', 'manual_link'),
    (266, 'London Borough of Hammersmith and Fulham', 'https://public-access.lbhf.gov.uk/online-applications', 'manual_link'),
    (239, 'London Borough of Harrow', 'https://www.harrow.gov.uk/online-applications', 'pending'),
    (236, 'London Borough of Hounslow', 'https://www.hounslow.gov.uk/online-applications', 'pending'),
    (244, 'London Borough of Islington', 'https://www.islington.gov.uk/online-applications', 'pending'),
    (234, 'London Borough of Kingston upon Thames', 'https://publicaccess.kingston.gov.uk/online-applications', 'manual_link'),
    (233, 'London Borough of Merton', 'https://www.merton.gov.uk/online-applications', 'pending'),
    (223, 'London Borough of Newham', 'https://pa.newham.gov.uk/online-applications', 'pending'),
    (219, 'London Borough of Southwark', 'https://planning.southwark.gov.uk/online-applications', 'manual_link'),
    (232, 'London Borough of Sutton', 'https://planningregister.sutton.gov.uk/online-applications', 'manual_link'),
    (319, 'Newport City Council', 'https://planning.newport.gov.uk/online-applications', 'manual_link'),
    (388, 'North East Lincolnshire Council', 'https://planninganddevelopment.nelincs.gov.uk/online-applications', 'manual_link'),
    (478, 'North Kesteven District Council', 'https://planningonline.n-kesteven.gov.uk/online-applications', 'manual_link'),
    (350, 'North Lanarkshire Council', 'https://eplanning.northlanarkshire.gov.uk/online-applications', 'manual_link'),
    (387, 'North Northamptonshire Council', 'https://publicaccess.northnorthants.gov.uk/online-applications', 'manual_link'),
    (371, 'North Yorkshire Council', 'https://publicaccess.northyorks.gov.uk/online-applications', 'manual_link'),
    (195, 'Nottinghamshire County Council', 'https://publicaccess.nottinghamshire.gov.uk/online-applications', 'pending'),
    (58, 'Oxford City Council', 'https://public.oxford.gov.uk/online-applications', 'manual_link'),
    (309, 'Preston City Council', 'https://publicaccess.preston.gov.uk/online-applications', 'pending'),
    (190, 'Sandwell Metropolitan Borough Council', 'https://webcaps.sandwell.gov.uk/publicaccess', 'manual_link'),
    (351, 'Scottish Borders Council', 'https://eplanning.scotborders.gov.uk/online-applications', 'manual_link'),
    (271, 'Sevenoaks District Council', 'https://pa.sevenoaks.gov.uk/online-applications', 'manual_link'),
    (63, 'Sheffield City Council', 'https://planningapps.sheffield.gov.uk/online-applications', 'manual_link'),
    (364, 'Shropshire Council', 'https://pa.shropshire.gov.uk/online-applications', 'manual_link'),
    (370, 'Somerset Council (Mendip)', 'https://publicaccess.mendip.gov.uk/online-applications', 'manual_link'),
    (339, 'South Ayrshire Council', 'https://publicaccess.south-ayrshire.gov.uk/online-applications', 'manual_link'),
    (257, 'South Downs National Park Authority', 'https://planningpublicaccess.southdowns.gov.uk/online-applications', 'manual_link'),
    (262, 'South Gloucestershire Council', 'https://developments.southglos.gov.uk/online-applications', 'manual_link'),
    (199, 'South Tyneside Metropolitan Borough Council', 'https://www.southtyneside.gov.uk/online-applications', 'pending'),
    (65, 'Southampton City Council', 'https://planningpublicaccess.southampton.gov.uk/online-applications', 'manual_link'),
    (181, 'St. Helens Metropolitan Borough Council', 'https://publicaccess.sthelens.gov.uk/online-applications', 'manual_link'),
    (476, 'Stafford Borough Council', 'https://www12.staffordbc.gov.uk/online-applications', 'pending'),
    (68, 'Sunderland City Council', 'https://online-applications.sunderland.gov.uk/online-applications', 'manual_link'),
    (373, 'Swansea Council', 'https://property.swansea.gov.uk/online-applications', 'manual_link'),
    (265, 'Tendring District Council', 'https://idox.tendringdc.gov.uk/online-applications', 'manual_link'),
    (291, 'Three Rivers District Council', 'https://www3.threerivers.gov.uk/online-applications', 'manual_link'),
    (418, 'Torridge District Council', 'https://publicaccess.torridge.gov.uk/online-applications', 'pending'),
    (288, 'Uttlesford District Council', 'https://publicaccess.uttlesford.gov.uk/online-applications', 'manual_link'),
    (449, 'Watford Borough Council', 'https://pa.watford.gov.uk/publicaccess', 'manual_link'),
    (258, 'West Berkshire Council', 'https://publicaccess.westberks.gov.uk/online-applications', 'manual_link'),
    (79, 'Wolverhampton City Council', 'https://planningonline.wolverhampton.gov.uk/online-applications', 'manual_link'),
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEAD = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"}
FORM = "/search.do?action=advanced&searchType=Application"
CHALLENGE = re.compile(r"just a moment|cf-chl|challenge-platform|attention required|incapsula|_incap_|request unsuccessful|access denied|"
                       r"pardon our interruption|are you a robot|perimeterx|akamai", re.I)
# PROXY_URLS: optional comma list of http://user:pass@host:port, used round-robin for the curl_cffi requests.
# ONLY_FAILED=1 re-tests only councils that are not already known-good (everything except the 4 that passed without a proxy).
PROXIES = [x.strip() for x in os.environ.get("PROXY_URLS", "").split(",") if x.strip()]
SEM = asyncio.Semaphore(min(8, max(2, len(PROXIES))) if PROXIES else 8)
_rr = [0]


def next_proxy():
    if not PROXIES:
        return None
    _rr[0] += 1
    p = PROXIES[_rr[0] % len(PROXIES)]
    return {"http": p, "https": p}


def title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return " ".join(m.group(1).split())[:50] if m else ""


def cards(html: str) -> int:
    return len(re.findall(r'<li[^>]*class="[^"]*searchresult', html))


async def baseline(base: str) -> str:
    try:
        async with httpx.AsyncClient(headers=HEAD, follow_redirects=True, timeout=25) as c:
            r = await c.get(base + FORM)
        if r.status_code == 200 and "searchCriteriaForm" in r.text:
            return "form"
        return f"HTTP {r.status_code}" + (" challenge" if CHALLENGE.search(r.text[:6000]) else "")
    except Exception as e:
        return type(e).__name__


async def probe(cid: int, name: str, base: str, src: str) -> dict:
    out = {"id": cid, "name": name, "base": base, "src": src, "plain": "", "verdict": "", "detail": ""}
    async with SEM:
        out["plain"] = "skipped (proxy)" if PROXIES else await baseline(base)
        s = None
        r = None
        err = None
        insecure = False
        for attempt in range(3 if PROXIES else 1):
            s = AsyncSession(impersonate="chrome", proxies=next_proxy(), verify=not insecure)
            try:
                r = await s.get(base + FORM, timeout=35)
                err = None
                break
            except Exception as e:
                err = e
                try:
                    await s.close()
                except Exception:
                    pass
                if "ertificate" in f"{type(e).__name__}{e}":
                    insecure = True
        try:
            if r is None:
                e = err
                out["verdict"] = "TIMEOUT" if "imeout" in f"{type(e).__name__}{e}" or "timed out" in str(e) else "ERROR"
                out["detail"] = f"{type(e).__name__}: {str(e)[:70]} (tried {attempt + 1} proxies)"
                return out
            if insecure:
                out["detail"] = "CERT CHAIN BAD (verify off needed) "
            final = urlparse(str(r.url)).netloc
            if final and final != urlparse(base).netloc:
                out["detail"] = f"redirected to {final}"
                out["verdict"] = "MOVED"
                return out
            head = r.text[:8000]
            if r.status_code != 200:
                out["verdict"] = "CHALLENGE" if CHALLENGE.search(head) else "ERROR"
                out["detail"] = f"HTTP {r.status_code} {title_of(r.text)!r}"
                return out
            if "searchCriteriaForm" not in r.text and "advancedSearchForm" not in r.text and "date(applicationReceivedStart)" not in r.text:
                out["verdict"] = "CHALLENGE" if CHALLENGE.search(head) else "NOT_IDOX"
                out["detail"] = f"title {title_of(r.text)!r}"
                return out
            recaptcha = "recaptcha" in r.text.lower()
            m = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text) or re.search(r'value="([^"]+)"\s+name="_csrf"', r.text)
            data = {"searchType": "Application", "searchCriteria.caseStatus": "",
                    "date(applicationReceivedStart)": (date.today() - timedelta(days=10)).strftime("%d/%m/%Y"),
                    "date(applicationReceivedEnd)": date.today().strftime("%d/%m/%Y")}
            if m:
                data["_csrf"] = m.group(1)
            try:
                p = await s.post(base + "/advancedSearchResults.do?action=firstPage", data=data, timeout=60,
                                 headers={"Referer": base + FORM})
            except Exception as e:
                out["verdict"], out["detail"] = "FORM_ONLY", f"POST {type(e).__name__}: {str(e)[:60]}"
                return out
            n = cards(p.text)
            low = p.text.lower()
            if p.status_code == 200 and n:
                out["verdict"] = "OK_HTTP"
                out["detail"] = out["detail"] + f"{n} result cards on page 1" + (" (recaptcha on form, not needed)" if recaptcha else "")
            elif p.status_code == 200 and ("no results" in low or "no applications" in low or "searchCriteriaForm" in p.text):
                out["verdict"] = "OK_HTTP"
                out["detail"] = "search accepted, 0 results in the last 10 days"
            else:
                out["verdict"] = "CHALLENGE" if CHALLENGE.search(p.text[:6000]) else "FORM_ONLY"
                out["detail"] = f"POST HTTP {p.status_code} {title_of(p.text)!r}" + (" recaptcha" if recaptcha else "")
            return out
        finally:
            try:
                await s.close()
            except Exception:
                pass


async def main() -> int:
    print(f"PROXY_URLS: {len(PROXIES)} proxies loaded" + ("" if PROXIES else "  <-- NONE, running direct from the runner IP (check the PROBE_PROXY_URLS secret and the job's env: block)"))
    for px in PROXIES:
        try:
            async with AsyncSession(impersonate="chrome", proxies={"http": px, "https": px}) as t:
                r = await t.get("https://api.ipify.org", timeout=20)
            print(f"proxy {px.split('@')[-1]} exits as {r.text.strip()}")
        except Exception as e:
            print(f"proxy {px.split('@')[-1]} FAILED: {type(e).__name__}: {str(e)[:80]}")
    print(f"Probing {len(COUNCILS)} Idox councils (curl_cffi Chrome fingerprint, {'via ' + str(len(PROXIES)) + ' proxies, ' if PROXIES else ''}{SEM._value} at a time)\n")
    results = await asyncio.gather(*(probe(*c) for c in COUNCILS))
    by = defaultdict(list)
    for r in results:
        by[r["verdict"]].append(r)
        print(f"  {r['verdict']:10s} {r['name'][:46]:46s} (id {r['id']}, {r['src']}) plain={r['plain']!r} {r['detail']}")
    print("\n== summary")
    for v in ("OK_HTTP", "FORM_ONLY", "CHALLENGE", "MOVED", "NOT_IDOX", "TIMEOUT", "ERROR"):
        lst = by.get(v, [])
        print(f"\n{v}: {len(lst)}")
        for r in lst:
            print(f"    {r['id']:>4} {r['name']} | {r['base']}" + (f" | {r['detail']}" if v != "OK_HTTP" else ""))
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
