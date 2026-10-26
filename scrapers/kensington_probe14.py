"""Kensington & Chelsea recon, round 14 (council id 45). Writes nothing.

Round 13: rendering the SEARCH PAGE through ScraperAPI works:
  https://www.rbkc.gov.uk/planningsearch/search/everywhere?dateFrom=<ms>&dateTo=<ms>   (render=true, wait_for_selector [id^=sr-])
  -> 'Everywhere Search Results | RBKC Planning Portal', summary '215 Planning applications in the area from 23 Oct 2026
     to 26 Oct 2026 / 162 applications 53 decisions 0 appeals', but only 100 <article id="sr-..." aria-setsize="100"> cards.
  Card text: 'Planning Granted Decision N°: PP/26/04432 <description> Granted 23 Oct 2026 <address>' and
             'Planning Consultation Application N°: PP/26/05539 <description> Open 23 Oct 2026 <address>'.

Open questions this probe answers (about 9 rendered requests):
  1. the cleaned markup of one Decision card and one Application card (to parse fields reliably)
  2. single days (a weekday, and an empty Sunday): do dateFrom == dateTo give exactly that day? what is the card date?
  3. does a `limit` or `page` URL parameter lift the 100-card cap?
  4. any 'load more' control in the list
  5. a rendered case detail page (/planningsearch/cases/PP/26/04432): does it show the received date?
  6. credit cost per rendered call (sa-credit-cost header)
"""
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

KEY = os.environ.get("SCRAPERAPI_KEY", "")
SITE = "https://www.rbkc.gov.uk"
API = "https://api.scraperapi.com/"


def utc_ms(dt):
    return int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp() * 1000)


def render(url, wait=True):
    params = {"api_key": KEY, "url": url, "country_code": "gb", "render": "true"}
    if wait:
        params["wait_for_selector"] = "[id^=sr-]"
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, timeout=200, follow_redirects=True)
    except Exception as e:
        print(f"    ERROR {str(e)[:200]}")
        return None
    cost = r.headers.get("sa-credit-cost") or r.headers.get("x-credits-cost") or "?"
    print(f"    HTTP {r.status_code} {len(r.content)}B credits={cost} ({time.time() - t0:.0f}s)")
    return r


def clean(el, limit=2500):
    """compact markup: drop class/style/svg paths, keep text, aria-label, title, datetime"""
    html = str(el)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup.find_all(True):
        keep = {k: v for k, v in t.attrs.items() if k in ("aria-label", "title", "datetime", "href", "id", "aria-describedby")}
        t.attrs = keep
    for p in soup.find_all("path"):
        p.decompose()
    s = re.sub(r"\s+", " ", str(soup))
    return s[:limit]


def summary_line(soup):
    txt = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    m = re.search(r"(\d[\d,]*) Planning applications in the area from ([\d A-Za-z]+) to ([\d A-Za-z]+)", txt)
    m2 = re.search(r"(\d+) applications (\d+) decisions (\d+) appeals", txt)
    return (m.group(0) if m else "(no summary)"), (m2.group(0) if m2 else "")


def card_info(c):
    ref = None
    desc = c.get("aria-describedby") or ""
    if desc.startswith("description-"):
        ref = desc[len("description-"):]
    svg = c.find("svg", attrs={"aria-label": True})
    txt = re.sub(r"\s+", " ", c.get_text(" ")).strip()
    return ref, (svg.get("aria-label") if svg else None), txt


def show_page(label, r, full_cards=0):
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select("article[id^=sr-]")
    sm = summary_line(soup)
    print(f"  {label}: {len(cards)} cards; {sm[0]} | {sm[1]}")
    sizes = set(c.get("aria-setsize") for c in cards)
    print(f"    aria-setsize values: {sizes}")
    dates = Counter()
    states = Counter()
    for c in cards:
        ref, state, txt = card_info(c)
        states[state] += 1
        m = re.search(r"\b(\d{1,2} [A-Z][a-z]{2} \d{4})\b", txt)
        dates[m.group(1) if m else "?"] += 1
    print(f"    states: {dict(states)}")
    print(f"    card dates: {dict(sorted(dates.items(), key=lambda kv: kv[0])[:12])}")
    for c in cards[:2]:
        print(f"    sample: {card_info(c)}")
    buttons = [re.sub(r'\s+', ' ', b.get_text(' ')).strip() for b in soup.find_all("button")]
    print(f"    buttons: {[b for b in buttons if b][:25]}")
    for i, c in enumerate(cards[:full_cards]):
        print(f"    --- cleaned markup of card {i}: {clean(c)}")
    return soup, cards


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1
    today = date.today()
    wk = today - timedelta(days=3)           # a recent weekday (Fri 23 Oct when run on Mon 26 Oct)
    while wk.weekday() >= 5:
        wk -= timedelta(days=1)
    sun = today - timedelta(days=1)          # Sunday 25 Oct
    while sun.weekday() != 6:
        sun -= timedelta(days=1)

    print(f"\n== 1/2. single weekday {wk} (dateFrom = dateTo = UTC midnight)")
    u = f"{SITE}/planningsearch/search/everywhere?dateFrom={utc_ms(wk)}&dateTo={utc_ms(wk)}"
    r = render(u)
    if r is not None and r.status_code == 200:
        show_page(f"{wk}", r, full_cards=0)
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("article[id^=sr-]")
        dec = next((c for c in cards if card_info(c)[1] == "Decision"), None)
        app = next((c for c in cards if card_info(c)[1] == "Application"), None)
        if dec is not None:
            print(f"    --- DECISION card markup: {clean(dec, 3500)}")
        if app is not None:
            print(f"    --- APPLICATION card markup: {clean(app, 3500)}")
        app_ref = card_info(app)[0] if app is not None else None
        dec_ref = card_info(dec)[0] if dec is not None else None
    else:
        app_ref = dec_ref = None

    print(f"\n== 2b. empty Sunday {sun}")
    r = render(f"{SITE}/planningsearch/search/everywhere?dateFrom={utc_ms(sun)}&dateTo={utc_ms(sun)}", wait=False)
    if r is not None and r.status_code == 200:
        show_page(f"{sun}", r)
        soup = BeautifulSoup(r.text, "html.parser")
        txt = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        print(f"    page text: {txt[:600]}")

    print("\n== 3. does limit / page lift the 100-card cap? (4-day window 23-26 Oct)")
    f4, t4 = utc_ms(today - timedelta(days=3)), utc_ms(today)
    base = f"{SITE}/planningsearch/search/everywhere?dateFrom={f4}&dateTo={t4}"
    for label, extra in (("no extras", ""), ("limit=250", "&limit=250"), ("limit=500", "&limit=500"), ("page=2", "&page=2")):
        print(f"  -- {label}")
        r = render(base + extra)
        if r is not None and r.status_code == 200:
            show_page(label, r)

    print("\n== 5. rendered case detail pages")
    for ref in [x for x in (dec_ref, app_ref) if x][:2]:
        for path in (f"/planningsearch/cases/{ref}",):
            print(f"  {path}")
            r = render(SITE + path, wait=False)
            if r is not None and r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                main_el = soup.find("main") or soup.body
                txt = re.sub(r"\s+", " ", main_el.get_text(" ")).strip()
                print(f"    title: {soup.title.get_text(strip=True) if soup.title else ''!r}")
                print(f"    text: {txt[:2500]}")
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
