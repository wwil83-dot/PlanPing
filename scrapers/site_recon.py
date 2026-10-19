#!/usr/bin/env python3
"""
PlanFind - GENERIC planning-portal RECON (writes NOTHING to the database).

Reusable for any council. Configure with environment variables (set in the workflow job):
  RECON_NAME      label for the log                          (e.g. "North Devon")
  RECON_URLS      comma-separated URLs; the FIRST is the portal start page
  RECON_TERM      search term to type into the search box    (default "2026")
  RECON_DETAIL_RE regex for an anchor TEXT or HREF that opens one application
                  (default: anything with '/Display/', 'applicationDetails', 'PlanAppDetail', 'ref=' ...)

What it logs:
  1. For every URL: status, server, the real <form> tags, inputs, iframes, script sources,
     and links that mention search / weekly / list / decision / application.
  2. The start page rendered in a headless browser: text, links, form fields.
  3. A search by RECON_TERM: the results page (text, links, fields).
  4. One application page opened from the results.
  5. Every background (XHR/fetch) request with its POST body and a JSON/HTML preview, so a
     data endpoint (as at Waverley, Eastbourne, Mole Valley) shows up if there is one.
"""
import asyncio
import os
import re
import urllib.error
import urllib.request

NAME = os.environ.get("RECON_NAME", "council")
URLS = [u.strip() for u in os.environ.get("RECON_URLS", "").split(",") if u.strip()]
TERM = os.environ.get("RECON_TERM", "2026")
DETAIL_RE = re.compile(os.environ.get(
    "RECON_DETAIL_RE",
    r"/Display/|applicationDetails|PlanAppDetail|PlanningDetail|[?&]ref=|[?&]id=\d|KeyNo=|DCAPR|/application/"), re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SKIP_TYPES = {"image", "font", "stylesheet", "media"}


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


def http(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:
        return None, {}, str(e).encode()


def raw_html_report(url):
    hr("RAW HTML: " + url)
    st, h, b = http(url)
    html = b.decode("utf-8", "replace")
    print("status", st, "len", len(html), "| server", h.get("Server"), "| type", h.get("Content-Type"),
          "| location", h.get("Location"))
    if st in (403, 503) or "challenge" in html.lower()[:3000]:
        print("   (looks like a bot challenge / block; the browser section below shows whether Chromium gets through)")
    forms = re.findall(r"<form\b[^>]*>", html, flags=re.I)
    print(f"--- <form> tags ({len(forms)}) ---")
    for f in forms:
        print("  ", re.sub(r"\s+", " ", f)[:300])
    print("--- inputs / selects ---")
    for m in list(re.finditer(r"<(input|select|textarea)\b([^>]*)>", html, flags=re.I))[:60]:
        attrs = m.group(2)
        pick = {k: (re.search(k + r'="([^"]*)"', attrs, flags=re.I) or [None, ""])[1]
                for k in ("name", "id", "type", "value", "placeholder")}
        print(f"   {m.group(1):<8} {pick}")
    print("--- iframes ---")
    for m in re.findall(r"<iframe\b[^>]*>", html, flags=re.I):
        print("  ", re.sub(r"\s+", " ", m)[:300])
    print("--- script src ---")
    for m in re.findall(r'<script[^>]+src="([^"]+)"', html, flags=re.I)[:40]:
        print("  ", m[:200])
    print("--- 'powered by' / generator / technology hints ---")
    for m in re.findall(r'(?:powered by[^<]{0,60}|<meta[^>]+name="generator"[^>]*>|Idox|Civica|Northgate|Arcus|Ocella|Agile|Statmap|Fastweb|Uniform|Planning Explorer|PlanningExplorer)', html, flags=re.I)[:15]:
        print("  ", re.sub(r"\s+", " ", m)[:140])
    print("--- anchors mentioning search / week / list / decision / application ---")
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S)[:400]:
        t = re.sub(r"<[^>]+>|\s+", " ", text).strip()
        if re.search(r"week|list|decision|application|search|appeal|advanced", t + href, flags=re.I):
            print(f"   {t[:70]!r} -> {href[:200]}")


async def dump_page(page, label, text_chars=3500):
    hr(label)
    print("URL:", page.url)
    print("TITLE:", await page.title())
    try:
        txt = await page.inner_text("body")
    except Exception as e:
        txt = f"(no body: {e})"
    print("--- visible text ---")
    print(re.sub(r"\n{3,}", "\n\n", txt)[:text_chars])
    for fr in page.frames:
        if fr != page.main_frame:
            print("FRAME:", fr.url)
    links = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e => [(e.innerText||'').trim().slice(0,70), e.href])")
    print(f"--- links ({len(links)}) first 70 ---")
    for t, h in links[:70]:
        print(f"  {t!r} -> {h}")
    fields = await page.eval_on_selector_all(
        "input, select, textarea, button",
        """els => els.map(e => [e.tagName, e.type||'', e.name||'', e.id||'', (e.placeholder||e.value||e.innerText||'').trim().slice(0,50),
            e.tagName==='SELECT' ? Array.from(e.options).slice(0,10).map(o=>o.text).join('|') : ''])""")
    print(f"--- form fields ({len(fields)}) ---")
    for f in fields[:60]:
        print("  ", f)


async def main():
    if not URLS:
        print("ERROR: set RECON_URLS")
        return
    print(f"Recon for {NAME}; start page {URLS[0]}; search term {TERM!r}")
    for u in URLS:
        raw_html_report(u)

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=UA, locale="en-GB", ignore_https_errors=True,
                                        viewport={"width": 1400, "height": 1000})
        page = await ctx.new_page()
        seen = []

        async def on_resp(resp):
            try:
                req = resp.request
                if req.resource_type in SKIP_TYPES:
                    return
                ct = resp.headers.get("content-type", "")
                entry = f"[{resp.status}] {req.method} {req.resource_type} {resp.url[:220]}  ({ct[:40]})"
                body = ""
                if req.resource_type in ("xhr", "fetch") or "json" in ct:
                    try:
                        body = (await resp.text())[:700]
                    except Exception:
                        body = "(unreadable)"
                if req.method == "POST" and req.post_data:
                    entry += f"\n      POST DATA: {req.post_data[:500]}"
                seen.append((entry, body))
            except Exception:
                pass

        page.on("response", on_resp)

        try:
            await page.goto(URLS[0], wait_until="networkidle", timeout=60000)
        except Exception as e:
            print("goto problem:", e)
        # Accept cookie banners if there is an obvious button
        for label in ("Accept all cookies", "Accept all", "Accept cookies", "I agree", "Accept"):
            try:
                b = page.get_by_role("button", name=re.compile(label, re.I)).first
                if await b.count() and await b.is_visible():
                    await b.click(timeout=3000)
                    await page.wait_for_timeout(800)
                    break
            except Exception:
                pass
        await dump_page(page, "1. START PAGE (rendered)")

        hr(f"2. Search for {TERM!r}")
        searched = False
        try:
            cands = page.locator("input[type=text]:visible, input[type=search]:visible, input:not([type]):visible")
            n = await cands.count()
            print("visible text inputs:", n)
            order = []
            for i in range(n):
                el = cands.nth(i)
                nm = " ".join([(await el.get_attribute("name")) or "", (await el.get_attribute("id")) or "",
                               (await el.get_attribute("placeholder")) or ""])
                print("   input", i, nm.strip())
                score = 2 if re.search(r"ref|addr|keyword|desc|postcode|case|application", nm, re.I) else \
                    (1 if re.search(r"search", nm, re.I) else 0)
                if re.search(r"site|cludo|q$", nm, re.I) and score < 2:
                    score = -1
                order.append((score, i))
            for score, i in sorted(order, reverse=True):
                if score < 0:
                    continue
                el = cands.nth(i)
                await el.fill(TERM)
                await el.press("Enter")
                searched = True
                print("typed into input", i)
                break
            if not searched:
                print("no suitable text input found")
        except Exception as e:
            print("search failed:", e)
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        await dump_page(page, "2. AFTER SEARCH", 5000)

        hr("3. First application page")
        try:
            link = None
            for t, h in await page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => [(e.innerText||'').trim(), e.href])"):
                if DETAIL_RE.search(h) or re.match(r"^[A-Z0-9/.\-]{5,}$", t or ""):
                    link = h
                    break
            if link:
                print("opening:", link)
                await page.goto(link, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(2500)
                await dump_page(page, "3. APPLICATION PAGE", 6000)
            else:
                print("no application link recognised on the results page")
        except Exception as e:
            print("application step failed:", e)

        hr("NETWORK LOG (non-static requests)")
        for entry, body in seen[:160]:
            print(entry)
            if body:
                print("      BODY:", re.sub(r"\s+", " ", body)[:600])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
