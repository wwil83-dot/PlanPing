"""Kensington & Chelsea recon, round 2 (council id 45). Writes nothing.

Round 1 showed www.rbkc.gov.uk answers plain HTTP with a Cloudflare 'Just a moment...' challenge (403).
Application pages do exist at https://www.rbkc.gov.uk/PP/24/05187 (reference in the path).

This probe tries to get past the challenge in four ways and reports which work:
  A  Playwright chromium, headless, light anti-detection tweaks
  B  Playwright chromium, headed (needs a display; the job wraps it in xvfb-run when available)
  C  Playwright chromium, headless, via the Webshare proxy
  D  Bright Data Browser API (CDP) using BRIGHTDATA_BROWSER_AUTH
For the first strategy that passes it dumps the weekly-list form, submits it for the latest Fridays, and opens one
application page (PP/24/05187) to show the labelled fields.
"""
import os
import re
import sys
import time
from datetime import date, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = "https://www.rbkc.gov.uk"
FORM_URL = f"{BASE}/planning/scripts/weeklyform.asp"
DETAIL_URL = f"{BASE}/PP/24/05187"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
INIT = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"


def show(label, text, n=700):
    t = re.sub(r"\s+", " ", text or "").strip()
    print(f"    {label}: {t[:n]}")


def blocked(page):
    try:
        t = page.title() or ""
    except Exception:
        return True
    return "just a moment" in t.lower() or "attention required" in t.lower()


def goto_wait(page, url, wait_s=30):
    """Load url and give a Cloudflare interstitial up to wait_s seconds to clear."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"    goto error: {str(e)[:150]}")
    t0 = time.time()
    while time.time() - t0 < wait_s and blocked(page):
        page.wait_for_timeout(1500)
    return not blocked(page)


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


def make_context(p, name):
    """Return (browser, context) for a strategy, or raise."""
    if name == "A":
        b = p.chromium.launch(headless=True, args=ARGS)
        ctx = b.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London",
                            viewport={"width": 1366, "height": 850})
    elif name == "B":
        b = p.chromium.launch(headless=False, args=ARGS)
        ctx = b.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London",
                            viewport={"width": 1366, "height": 850})
    elif name == "C":
        host = os.environ.get("WEBSHARE_PROXY_HOST")
        port = os.environ.get("WEBSHARE_PROXY_PORT")
        user = os.environ.get("WEBSHARE_PROXY_USERNAME")
        pwd = os.environ.get("WEBSHARE_PROXY_PASSWORD")
        if not (host and port and user and pwd):
            raise RuntimeError("Webshare secrets not set")
        b = p.chromium.launch(headless=True, args=ARGS,
                              proxy={"server": f"http://{host}:{port}", "username": user, "password": pwd})
        ctx = b.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London",
                            viewport={"width": 1366, "height": 850})
    elif name == "D":
        auth = os.environ.get("BRIGHTDATA_BROWSER_AUTH", "")
        if not auth:
            raise RuntimeError("BRIGHTDATA_BROWSER_AUTH not set")
        endpoint = auth if auth.startswith("wss://") else f"wss://{auth}@brd.superproxy.io:9222"
        b = p.chromium.connect_over_cdp(endpoint)
        ctx = b.contexts[0] if b.contexts else b.new_context()
    else:
        raise ValueError(name)
    ctx.add_init_script(INIT) if name in "ABC" else None
    return b, ctx


def fridays(n=3):
    d = date.today()
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return [d - timedelta(days=7 * i) for i in range(n)]


def deep_dump(page):
    print("\n  ---- weekly form")
    soup = BeautifulSoup(page.content(), "html.parser")
    show("title", soup.title.get_text() if soup.title else "")
    show("text", soup.get_text(" "), 1200)
    dump_forms(soup)
    # try to drive the form: choose a Friday option / fill a date, press submit
    for fd in fridays(3):
        if not goto_wait(page, FORM_URL, 20):
            print("    form page blocked on reload")
            break
        sel = page.query_selector("select")
        chosen = None
        if sel:
            opts = page.eval_on_selector_all("select option", "els => els.map(e => [e.value, e.textContent.trim()])")
            variants = [fd.strftime(f) for f in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %B %Y", "%d/%m/%y", "%-d %B %Y")]
            for v, t in opts:
                if any(x.lower() in (v + " " + t).lower() for x in variants):
                    chosen = v
                    break
            if chosen is None and len(opts) > 1:
                chosen = opts[1][0]
                print(f"    no option matched {fd}; using {opts[1]}")
            if chosen is not None:
                page.select_option("select", chosen)
        else:
            inp = page.query_selector("input[type=text], input[type=date], input:not([type])")
            if inp:
                inp.fill(fd.strftime("%d/%m/%Y"))
                chosen = fd.strftime("%d/%m/%Y")
        btn = page.query_selector("input[type=submit], button[type=submit], button, input[type=image]")
        if not btn:
            print("    no submit button")
            break
        try:
            with page.expect_navigation(timeout=30000):
                btn.click()
        except Exception as e:
            print(f"    submit: {str(e)[:120]}")
        goto_wait(page, page.url, 15)
        s2 = BeautifulSoup(page.content(), "html.parser")
        rows = s2.find_all("tr")
        print(f"  {fd} value={chosen!r}: url={page.url} tables={len(s2.find_all('table'))} rows={len(rows)}")
        if len(rows) > 3:
            show("text", s2.get_text(" "), 1500)
            for ti, t in enumerate(s2.find_all("table")[:3]):
                trs = t.find_all("tr")
                print(f"   table {ti}: {len(trs)} rows")
                for tr in trs[:4]:
                    show("row", tr.get_text(" | ", strip=True), 400)
            n = 0
            for a in s2.find_all("a", href=True):
                print(f"      link {urljoin(page.url, a['href'])}  [{a.get_text(' ', strip=True)[:50]}]")
                n += 1
                if n >= 12:
                    break
            open("/tmp/kc_weekly.html", "w", encoding="utf-8").write(page.content())
            break

    print("\n  ---- detail page")
    if goto_wait(page, DETAIL_URL, 20):
        s3 = BeautifulSoup(page.content(), "html.parser")
        show("text", s3.get_text(" "), 3000)
    else:
        print("    detail page blocked")


def main():
    only = os.environ.get("STRATEGIES", "ABCD")
    winner = None
    with sync_playwright() as p:
        for name in only:
            print(f"\n== strategy {name}")
            try:
                b, ctx = make_context(p, name)
            except Exception as e:
                print(f"  setup failed: {str(e)[:200]}")
                continue
            try:
                page = ctx.new_page()
                ok = goto_wait(page, FORM_URL, 35)
                title = page.title()
                print(f"  weekly form: title={title!r} passed={ok} url={page.url}")
                if ok and winner is None:
                    winner = name
                    deep_dump(page)
                elif ok:
                    print("  (already have a working strategy; skipping dump)")
            except Exception as e:
                print(f"  error: {str(e)[:250]}")
            finally:
                try:
                    b.close()
                except Exception:
                    pass
    print(f"\nWorking strategy: {winner}")
    print("Done.")


if __name__ == "__main__":
    sys.exit(main())
