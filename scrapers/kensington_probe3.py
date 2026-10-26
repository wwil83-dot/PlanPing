"""Kensington & Chelsea recon, round 3 (council id 45). Writes nothing.

Rounds 1-2: rbkc.gov.uk answers plain HTTP, headless/headed Playwright and the Webshare proxy with a Cloudflare
'Just a moment...' challenge; Bright Data refuses the site as 'Government'. This round tries ScraperAPI
(secret SCRAPERAPI_KEY) at increasing tiers, each against the weekly-list form and one application page, and reports
which tier returns the real page. For the first tier that works it dumps the form and the application page.
"""
import os
import re
import sys
import time

import httpx
from bs4 import BeautifulSoup

KEY = os.environ.get("SCRAPERAPI_KEY", "")
FORM_URL = "https://www.rbkc.gov.uk/planning/scripts/weeklyform.asp"
DETAIL_URL = "https://www.rbkc.gov.uk/PP/24/05187"
API = "https://api.scraperapi.com/"

TIERS = [
    ("plain", {}),
    ("render", {"render": "true"}),
    ("premium", {"premium": "true"}),
    ("premium+render", {"premium": "true", "render": "true"}),
    ("ultra_premium", {"ultra_premium": "true"}),
    ("ultra_premium+render", {"ultra_premium": "true", "render": "true"}),
]


def show(label, text, n=700):
    t = re.sub(r"\s+", " ", text or "").strip()
    print(f"    {label}: {t[:n]}")


def fetch(url, extra):
    params = {"api_key": KEY, "url": url, "country_code": "gb"}
    params.update(extra)
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, timeout=120, follow_redirects=True)
    except Exception as e:
        return None, f"ERROR {str(e)[:150]}", time.time() - t0
    return r, None, time.time() - t0


def good(r):
    if r is None or r.status_code != 200:
        return False
    t = r.text.lower()
    return "just a moment" not in t[:2000] and "enable javascript and cookies" not in t[:4000]


def dump_forms(soup):
    forms = soup.find_all("form")
    print(f"  {len(forms)} form(s)")
    for i, f in enumerate(forms):
        print(f"  --- form {i}: action={f.get('action')!r} method={f.get('method')!r} id={f.get('id')!r}")
        for el in f.find_all(["input", "select", "textarea", "button"]):
            if el.name == "select":
                opts = [(o.get("value"), o.get_text(strip=True)) for o in el.find_all("option")]
                print(f"      select name={el.get('name')!r} id={el.get('id')!r} {len(opts)} options")
                for v, t in opts[:10]:
                    print(f"          {v!r}: {t!r}")
                if len(opts) > 10:
                    print(f"          ... last: {opts[-1]!r}")
            else:
                print(f"      {el.name} type={el.get('type')!r} name={el.get('name')!r} id={el.get('id')!r} "
                      f"value={(el.get('value') or '')[:60]!r}")


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1
    winner = None
    for name, extra in TIERS:
        print(f"\n== tier {name}")
        r, err, dt = fetch(FORM_URL, extra)
        if err:
            print(f"  form: {err} ({dt:.0f}s)")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        print(f"  form: HTTP {r.status_code} {len(r.text)}b title={title!r} forms={len(soup.find_all('form'))} ({dt:.0f}s)"
              f" credits={r.headers.get('sa-credit-cost') or r.headers.get('x-credits-cost') or '?'}")
        if not good(r):
            show("body", soup.get_text(" "), 250)
            continue
        winner = (name, extra)
        print("  PASSED. Dumping the weekly form:")
        show("text", soup.get_text(" "), 1500)
        dump_forms(soup)
        r2, err2, dt2 = fetch(DETAIL_URL, extra)
        if err2:
            print(f"  detail: {err2}")
        else:
            s3 = BeautifulSoup(r2.text, "html.parser")
            print(f"  detail: HTTP {r2.status_code} {len(r2.text)}b title={(s3.title.get_text(strip=True) if s3.title else '')!r} ({dt2:.0f}s)")
            show("text", s3.get_text(" "), 3000)
        open("/tmp/kc_form.html", "w", encoding="utf-8").write(r.text)
        break
    print(f"\nWorking tier: {winner[0] if winner else None}")
    print("Done.")


if __name__ == "__main__":
    sys.exit(main())
