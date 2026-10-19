#!/usr/bin/env python3
"""
PlanFind - Eastbourne Borough Council RECON (writes NOTHING to the database).

Eastbourne's planning search lives inside the shared Lewes/Eastbourne website
(www.lewes-eastbourne.gov.uk, a GOSS CMS), not on an Idox/Civica portal we know:
  /planning                          landing page with Quick Search + 'Weekly List of Applications/Decisions'
  /article/2086/Search-Applications  results
  /article/2087/Application-Summary  one application
  /article/2101/Advanced-Search      advanced search
Page text says: current references look like 100000, pre-June-2013 like EB/2018/0000; postcodes
need a space; no hyphens in criteria. WebFetch strips forms, so this recon reads the real HTML.

Questions:
  1. What are the real <form> actions, field names, and any embedded iframe/script endpoints?
  2. Where are the Weekly List of Applications / Decisions (links, formats)?
  3. What do a quick search and a results page look like (fields, paging, URL pattern)?
  4. What does one application page contain, and is there a stable URL for it?
  5. Which background (XHR/fetch) requests happen?  Is there a JSON/HTML data endpoint?
Send the whole log back.
"""
import asyncio
import re
import urllib.error
import urllib.request

ROOT = "https://www.lewes-eastbourne.gov.uk"
PAGES = [ROOT + "/planning",
         ROOT + "/article/2086/Search-Applications",
         ROOT + "/article/2101/Advanced-Search",
         ROOT + "/article/2087/Application-Summary"]
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
    print("status", st, "len", len(html), "server", h.get("Server"), "| content-type", h.get("Content-Type"))
    forms = re.findall(r"<form\b[^>]*>", html, flags=re.I)
    print(f"--- <form> tags ({len(forms)}) ---")
    for f in forms:
        print("  ", re.sub(r"\s+", " ", f)[:300])
    print("--- <input>/<select>/<textarea> (name/id/type/value) ---")
    for m in re.finditer(r"<(input|select|textarea)\b([^>]*)>", html, flags=re.I):
        attrs = m.group(2)
        pick = {k: (re.search(k + r'="([^"]*)"', attrs, flags=re.I) or [None, ""])[1]
                for k in ("name", "id", "type", "value", "placeholder")}
        print(f"   {m.group(1):<8} {pick}")
    print("--- iframes ---")
    for m in re.findall(r"<iframe\b[^>]*>", html, flags=re.I):
        print("  ", re.sub(r"\s+", " ", m)[:300])
    print("--- script src ---")
    for m in re.findall(r'<script[^>]+src="([^"]+)"', html, flags=re.I):
        print("  ", m[:200])
    print("--- interesting URLs/strings in the HTML ---")
    seen = set()
    for m in re.findall(r'(?:https?:)?//[^\s"\'<>]+|/[A-Za-z0-9_./-]*(?:planning|Planning|PLANNING|search|Search|weekly|Weekly|applic|Applic|decision|Decision)[A-Za-z0-9_./?=&%-]*', html):
        if m not in seen and not re.search(r"\.(png|jpg|gif|css|ico|svg|woff2?)", m, flags=re.I):
            seen.add(m)
    for m in sorted(seen)[:80]:
        print("  ", m[:220])
    print("--- anchor hrefs mentioning list/weekly/decision/applications ---")
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
        t = re.sub(r"<[^>]+>|\s+", " ", text).strip()
        if re.search(r"week|list|decision|application|search|appeal", t + href, flags=re.I):
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
    print(f"--- links ({len(links)}) first 80 ---")
    for t, h in links[:80]:
        print(f"  {t!r} -> {h}")
    fields = await page.eval_on_selector_all(
        "input, select, textarea, button",
        """els => els.map(e => [e.tagName, e.type||'', e.name||'', e.id||'', (e.placeholder||e.value||e.innerText||'').trim().slice(0,50),
            e.tagName==='SELECT' ? Array.from(e.options).slice(0,10).map(o=>o.text).join('|') : ''])""")
    print(f"--- form fields ({len(fields)}) ---")
    for f in fields[:70]:
        print("  ", f)


async def main():
    for u in PAGES:
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

        await page.goto(ROOT + "/planning", wait_until="networkidle", timeout=60000)
        await dump_page(page, "1. /planning (rendered)")

        # Weekly list links on the rendered page
        hr("2. Weekly list / decision links on the rendered page")
        wl = await page.eval_on_selector_all(
            "a[href]", """els => els.filter(e => /week|list|decision|appeal/i.test(e.innerText + e.href))
                           .map(e => [(e.innerText||'').trim().slice(0,80), e.href])""")
        for t, h in wl:
            print(f"  {t!r} -> {h}")

        # Quick search with a postcode (documented format: needs the space)
        hr("3. Quick search: postcode 'BN21 4TW'")
        try:
            box = page.locator("input[type=text], input[type=search]").filter(has_not=page.locator("[name*=site i]")).first
            cands = page.locator("form:has(input[type=text]) input[type=text]")
            n = await cands.count()
            print("text inputs on page:", n)
            for i in range(n):
                el = cands.nth(i)
                print("   input", i, await el.get_attribute("name"), await el.get_attribute("id"),
                      await el.get_attribute("placeholder"))
            done = False
            for i in range(n):
                el = cands.nth(i)
                nm = ((await el.get_attribute("name")) or "") + ((await el.get_attribute("id")) or "")
                if re.search(r"site|query|keyword", nm, flags=re.I) and "plan" not in nm.lower():
                    continue
                if await el.is_visible():
                    await el.fill("BN21 4TW")
                    await el.press("Enter")
                    done = True
                    print("filled input", i, nm)
                    break
            if not done:
                print("no suitable quick-search input found")
        except Exception as e:
            print("quick search failed:", e)
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        await dump_page(page, "3. AFTER QUICK SEARCH", 5000)

        # Open the first result that looks like an application link
        hr("4. First application page")
        try:
            link = page.locator("a", has_text=re.compile(r"^\s*(\d{6}|EB/\d{4}/\d+)")).first
            if not await link.count():
                link = page.locator("a[href*='Application-Summary'], a[href*='2087']").first
            if await link.count():
                print("opening:", (await link.inner_text())[:80], "->", await link.get_attribute("href"))
                await link.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass
                await page.wait_for_timeout(2500)
                await dump_page(page, "4. APPLICATION PAGE", 6000)
            else:
                print("no application link recognised on the results page")
        except Exception as e:
            print("application step failed:", e)

        # Advanced search page rendered
        hr("5. Advanced search (rendered)")
        try:
            await page.goto(ROOT + "/article/2101/Advanced-Search", wait_until="networkidle", timeout=60000)
            await dump_page(page, "5. ADVANCED SEARCH", 3500)
        except Exception as e:
            print("advanced search failed:", e)

        hr("NETWORK LOG (non-static requests)")
        for entry, body in seen[:150]:
            print(entry)
            if body:
                print("      BODY:", re.sub(r"\s+", " ", body)[:600])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
