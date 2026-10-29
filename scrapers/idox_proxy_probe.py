"""
One-off probe, round 2 (2026-10-29): the Idox councils that were NOT reachable in the 2026-10-28 probe.

That probe gave every council ONE random proxy. Several councils answered some proxies and not others (Aberdeen, Watford,
Wolverhampton and Scottish Borders all failed on their first proxies in the real scraper and worked on a later one), so
a single try proved little. This probe tries up to ALL of the UK proxies, in turn, for each council and reports:

  * the first proxy that gets the real advanced-search form (and a 14-day search that returns result cards),
  * what each proxy that failed did (T timeout, X connection closed, E certificate error, C challenge, 4 HTTP 404, H other
    HTTP error, . not tried), so we can see whether a council blocks every proxy or only some,
  * for certificate errors, whether it works with certificate verification off (public planning pages, no logins),
  * for councils that work: whether the detail page shows a Decision field (decides whether outcomes can be read),
  * Stafford: a 60-day search, to see whether the single result earlier was a real search result or a redirect.

Writes NOTHING to the database. Env: PROXY_URLS (comma list, http://user:pass@host:port).
"""
import asyncio
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib.parse import urlparse, urljoin

from curl_cffi.requests import AsyncSession

# (id, name, base url)  -- every council from the 2026-10-28 probe that was not OK_HTTP, with corrected addresses
COUNCILS = [
    (296, "Gosport Borough Council", "https://publicaccess.gosport.gov.uk/online-applications"),
    (0, "Melton Borough Council", "https://pa.melton.gov.uk/online-applications"),
    (392, "Bassetlaw District Council", "https://publicaccess.bassetlaw.gov.uk/online-applications"),
    (282, "Braintree District Council", "https://publicaccess.braintree.gov.uk/online-applications"),
    (303, "Breckland District Council", "https://planning.breckland.gov.uk/online-applications"),
    (20, "Brighton and Hove City Council", "https://planningapps.brighton-hove.gov.uk/online-applications"),
    (176, "Calderdale Metropolitan Borough Council", "https://portal.calderdale.gov.uk/online-applications"),
    (26, "Cardiff Council", "https://www.cardiffidoxcloud.wales/publicaccess"),
    (322, "Carmarthenshire County Council", "https://planning.carmarthenshire.gov.uk/online-applications"),
    (342, "Clackmannanshire Council", "https://publicaccess.clacks.gov.uk/publicaccess"),
    (32, "Derby City Council", "https://eplanning.derby.gov.uk/online-applications"),
    (324, "Eryri National Park Authority", "https://pa.eryri.llyw.cymru/publicaccess"),
    (37, "Gloucester City Council", "https://publicaccess.gloucester.gov.uk/online-applications"),
    (360, "Harborough District Council", "https://pa2.harborough.gov.uk/online-applications"),
    (285, "Harlow District Council", "https://planningonline.harlow.gov.uk/online-applications"),
    (308, "Lancaster City Council", "https://planning.lancaster.gov.uk/online-applications"),
    (228, "London Borough of Bexley", "https://pa.bexley.gov.uk/online-applications"),
    (266, "London Borough of Hammersmith and Fulham", "https://public-access.lbhf.gov.uk/online-applications"),
    (234, "London Borough of Kingston upon Thames", "https://publicaccess.kingston.gov.uk/online-applications"),
    (223, "London Borough of Newham", "https://pa.newham.gov.uk/online-applications"),
    (219, "London Borough of Southwark", "https://planning.southwark.gov.uk/online-applications"),
    (232, "London Borough of Sutton", "https://planningregister.sutton.gov.uk/online-applications"),
    (319, "Newport City Council", "https://planning.newport.gov.uk/online-applications"),
    (388, "North East Lincolnshire Council", "https://planninganddevelopment.nelincs.gov.uk/online-applications"),
    (478, "North Kesteven District Council", "https://planningonline.n-kesteven.gov.uk/online-applications"),
    (350, "North Lanarkshire Council", "https://eplanning.northlanarkshire.gov.uk/online-applications"),
    (387, "North Northamptonshire Council", "https://publicaccess.northnorthants.gov.uk/online-applications"),
    (371, "North Yorkshire Council", "https://publicaccess.northyorks.gov.uk/online-applications"),
    (58, "Oxford City Council", "https://public.oxford.gov.uk/online-applications"),
    (309, "Preston City Council", "https://publicaccess.preston.gov.uk/online-applications"),
    (190, "Sandwell Metropolitan Borough Council", "https://webcaps.sandwell.gov.uk/publicaccess"),
    (271, "Sevenoaks District Council", "https://pa.sevenoaks.gov.uk/online-applications"),
    (63, "Sheffield City Council", "https://planningapps.sheffield.gov.uk/online-applications"),
    (364, "Shropshire Council", "https://pa.shropshire.gov.uk/online-applications"),
    (370, "Somerset Council (Mendip)", "https://publicaccess.mendip.gov.uk/online-applications"),
    (339, "South Ayrshire Council", "https://publicaccess.south-ayrshire.gov.uk/online-applications"),
    (262, "South Gloucestershire Council", "https://developments.southglos.gov.uk/online-applications"),
    (65, "Southampton City Council", "https://planningpublicaccess.southampton.gov.uk/online-applications"),
    (181, "St. Helens Metropolitan Borough Council", "https://publicaccess.sthelens.gov.uk/online-applications"),
    (476, "Stafford Borough Council", "https://www12.staffordbc.gov.uk/online-applications"),
    (68, "Sunderland City Council", "https://online-applications.sunderland.gov.uk/online-applications"),
    (373, "Swansea Council", "https://property.swansea.gov.uk/online-applications"),
    (265, "Tendring District Council", "https://idox.tendringdc.gov.uk/online-applications"),
    (291, "Three Rivers District Council", "https://www3.threerivers.gov.uk/online-applications"),
    (288, "Uttlesford District Council", "https://publicaccess.uttlesford.gov.uk/online-applications"),
    (258, "West Berkshire Council", "https://publicaccess.westberks.gov.uk/online-applications"),
]

FORM = "/search.do?action=advanced&searchType=Application"
CHALLENGE = re.compile(r"just a moment|cf-chl|challenge-platform|attention required|incapsula|_incap_|request unsuccessful|access denied|"
                       r"pardon our interruption|are you a robot|perimeterx|akamai", re.I)
PROXIES = [x.strip() for x in os.environ.get("PROXY_URLS", "").split(",") if x.strip()]
SEM = asyncio.Semaphore(6)
CONNECT_TIMEOUT = 20


def title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return " ".join(m.group(1).split())[:50] if m else ""


def cards(html: str) -> int:
    return len(re.findall(r'<li[^>]*class="[^"]*searchresult', html))


def px_dict(i):
    if i is None:
        return None
    return {"http": PROXIES[i], "https": PROXIES[i]}


def code_for(e: Exception) -> str:
    t = f"{type(e).__name__}{e}"
    if "ertificate" in t:
        return "E"
    if "imeout" in t or "timed out" in t:
        return "T"
    return "X"


async def get_form(base, pi, insecure):
    """Returns (letter, response_or_None, error_text). letter K = real form."""
    s = AsyncSession(impersonate="chrome", proxies=px_dict(pi), verify=not insecure)
    try:
        try:
            r = await s.get(base + FORM, timeout=CONNECT_TIMEOUT)
        except Exception as e:
            await s.close()
            return code_for(e), None, f"{type(e).__name__}: {str(e)[:60]}", None
        if r.status_code == 200 and ("searchCriteriaForm" in r.text or "advancedSearchForm" in r.text
                                     or "date(applicationReceivedStart)" in r.text):
            return "K", r, "", s
        letter = "C" if CHALLENGE.search(r.text[:6000]) else ("4" if r.status_code == 404 else "H")
        await s.close()
        return letter, None, f"HTTP {r.status_code} {title_of(r.text)!r}", None
    except Exception as e:
        return "X", None, f"{type(e).__name__}: {str(e)[:60]}", None


def detail_fields(html: str) -> dict:
    f = {}
    for m in re.finditer(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", html, re.I | re.S):
        k = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).lower().rstrip(":").split())
        v = " ".join(re.sub(r"<[^>]+>", " ", m.group(2)).split())
        if k:
            f.setdefault(k, v)
    return f


async def search(s, base, days, form_html):
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', form_html) or re.search(r'value="([^"]+)"\s+name="_csrf"', form_html)
    data = {"searchType": "Application", "searchCriteria.caseStatus": "",
            "date(applicationReceivedStart)": (date.today() - timedelta(days=days)).strftime("%d/%m/%Y"),
            "date(applicationReceivedEnd)": date.today().strftime("%d/%m/%Y")}
    if m:
        data["_csrf"] = m.group(1)
    return await s.post(base + "/advancedSearchResults.do?action=firstPage", data=data, timeout=60,
                        headers={"Referer": base + FORM})


async def probe(cid, name, base):
    out = {"id": cid, "name": name, "base": base, "trail": [], "verdict": "", "detail": "", "proxy": None, "insecure": False,
           "outcome_field": ""}
    n = max(1, len(PROXIES))
    order = [(cid + k) % n for k in range(n)] if PROXIES else [None]
    trail = ["."] * len(order)
    async with SEM:
        for k, pi in enumerate(order):
            letter, r, err, s = await get_form(base, pi, False)
            if letter == "E":
                letter2, r, err2, s = await get_form(base, pi, True)
                if letter2 == "K":
                    out["insecure"] = True
                    letter, err = "K", err2
                else:
                    err = err + " (also fails with verify off: " + err2 + ")"
            trail[k] = letter
            out["detail"] = err or out["detail"]
            if letter != "K":
                continue
            # form reachable on this proxy
            out["proxy"] = pi
            try:
                days = 60 if cid == 476 else 14
                p = await search(s, base, days, r.text)
                nc = cards(p.text)
                if p.status_code == 200 and nc:
                    out["verdict"] = "OK_HTTP"
                    out["detail"] = f"{nc} result cards on page 1 ({days} days)" + (" [verify off]" if out["insecure"] else "")
                    mm = re.search(r'href="([^"]*applicationDetails\.do\?[^"]*activeTab=summary[^"]*|[^"]*applicationDetails\.do\?[^"]+)"', p.text)
                    if mm:
                        href = mm.group(1).replace("&amp;", "&")
                        d = await s.get(urljoin(base + "/", href), timeout=40)
                        f = detail_fields(d.text) if d.status_code == 200 else {}
                        out["outcome_field"] = ("decision field present" if f.get("decision") else
                                                ("status only: " + f.get("status", "?")[:30] if f else "detail page not readable"))
                elif p.status_code == 200 and ("applicationDetails.do" in str(p.url)):
                    out["verdict"] = "OK_HTTP"
                    out["detail"] = "single result, redirected straight to the application page (search works)"
                elif p.status_code == 200 and ("no results" in p.text.lower() or "searchCriteriaForm" in p.text):
                    out["verdict"] = "OK_HTTP"
                    out["detail"] = f"search accepted, 0 results in {days} days"
                else:
                    out["verdict"] = "FORM_ONLY"
                    out["detail"] = f"POST HTTP {p.status_code} {title_of(p.text)!r}"
            except Exception as e:
                out["verdict"] = "FORM_ONLY"
                out["detail"] = f"POST {type(e).__name__}: {str(e)[:60]}"
            finally:
                try:
                    await s.close()
                except Exception:
                    pass
            break
        else:
            c = Counter(x for x in trail if x != ".")
            top = c.most_common(1)[0][0] if c else "X"
            out["verdict"] = {"T": "TIMEOUT_ALL", "X": "CLOSED_ALL", "E": "CERT_ALL", "C": "CHALLENGE_ALL", "4": "NOT_FOUND", "H": "HTTP_ERROR"}.get(top, "FAILED")
    out["trail"] = "".join(trail)
    return out


async def main() -> int:
    print(f"PROXY_URLS: {len(PROXIES)} proxies loaded" + ("" if PROXIES else "  <-- NONE, running direct (check the secret and the job env block)"))
    for i, px in enumerate(PROXIES):
        try:
            async with AsyncSession(impersonate="chrome", proxies=px_dict(i)) as t:
                r = await t.get("https://api.ipify.org", timeout=20)
            print(f"  proxy {i}: {px.split('@')[-1]} exits as {r.text.strip()}")
        except Exception as e:
            print(f"  proxy {i}: {px.split('@')[-1]} FAILED {type(e).__name__}")
    print(f"\nProbing {len(COUNCILS)} councils, up to {max(1, len(PROXIES))} proxies each, {SEM._value} councils at a time\n")
    results = await asyncio.gather(*(probe(*c) for c in COUNCILS))
    by = defaultdict(list)
    for r in results:
        by[r["verdict"]].append(r)
        print(f"  {r['verdict']:14s} {r['name'][:44]:44s} (id {r['id']}) proxies[{r['trail']}] "
              f"{'worked on proxy ' + str(r['proxy']) + ' ' if r['proxy'] is not None else ''}{r['detail']}"
              f"{'  | ' + r['outcome_field'] if r['outcome_field'] else ''}")
    print("\n== summary (trail letters: K ok, T timeout, X closed, E cert, C challenge, 4 404, H other http, . not tried)")
    order = ["OK_HTTP", "FORM_ONLY", "CHALLENGE_ALL", "CERT_ALL", "CLOSED_ALL", "NOT_FOUND", "HTTP_ERROR", "TIMEOUT_ALL", "FAILED"]
    for v in order:
        lst = by.get(v, [])
        if not lst:
            continue
        print(f"\n{v}: {len(lst)}")
        for r in lst:
            print(f"    {r['id']:>4} {r['name']} | {r['base']} | [{r['trail']}]" +
                  (f" | proxy {r['proxy']}" if r["proxy"] is not None else "") + (" | verify off needed" if r["insecure"] else "") +
                  (f" | {r['outcome_field']}" if r["outcome_field"] else "") + f" | {r['detail']}")
    ok = [r for r in results if r["proxy"] is not None]
    if ok:
        print("\nproxy -> councils it got through:", dict(sorted(Counter(r["proxy"] for r in ok).items())))
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
