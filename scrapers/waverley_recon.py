#!/usr/bin/env python3
"""
PlanFind - Waverley Borough Council RECON (writes NOTHING to the database).

Portal: https://planning360.waverley.gov.uk:4443/planning  (UK/Ireland only, non-standard port)
Weekly lists: PDFs on waverley.gov.uk (valid applications only, odd file names).

Questions this answers:
  0. Does the runner's IP get in at all (UK-only portal)?  What is the egress IP/country?
  1. What does the portal start page look like (platform, links, form fields)?
  2. What background (XHR/fetch) requests does a search make?  Is there a JSON API?
  3. What does a results page and a detail page contain (reference, address, proposal,
     dates, status, decision, coordinates)?
  4. What do the weekly-list PDFs contain?
Send the whole log back.
"""
import asyncio, json, re, sys, urllib.request, urllib.error

START = "https://planning360.waverley.gov.uk:4443/planning"
WEEKLY_PAGE = "https://www.waverley.gov.uk/planningweeklylist/"
PDF = ("https://www.waverley.gov.uk/Portals/0/Documents/services/planning-and-building/"
       "planning-applications-and-enforcement/planning applications/Weekly lists/weekly_list_2026-10-12.pdf")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SKIP_TYPES = {"image", "font", "stylesheet", "media"}


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


def http(url, n=600, binary=False):
    req = urllib.request.Request(url.replace(" ", "%20"), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = r.read()
            return r.status, dict(r.headers), data
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:
        return None, {}, str(e).encode()


def step0():
    hr("0. Egress IP / reachability")
    st, h, b = http("https://ipinfo.io/json")
    print("ipinfo:", st, b[:300].decode("utf-8", "replace"))
    st, h, b = http(START)
    print("GET start page:", st, "len", len(b))
    for k in ("Server", "Content-Type", "Set-Cookie", "X-Powered-By", "Location"):
        if k in h:
            print(f"  {k}: {h[k][:160]}")
    print("  body head:", re.sub(r"\s+", " ", b[:700].decode("utf-8", "replace")))


def step4():
    hr("4. Weekly list page + latest PDF")
    st, h, b = http(WEEKLY_PAGE)
    print("weekly page:", st, "len", len(b))
    links = re.findall(r'href="([^"]+\.pdf)"', b.decode("utf-8", "replace"), flags=re.I)
    print("pdf links found:", len(links))
    for l in links[:12]:
        print("  ", l)
    st, h, b = http(PDF)
    print("latest PDF:", st, h.get("Content-Type"), "bytes", len(b))
    if st == 200 and b[:4] == b"%PDF":
        try:
            import io
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(b))
            print("pages:", len(rd.pages))
            txt = rd.pages[0].extract_text() or ""
            print("--- page 1 text (first 2500 chars) ---")
            print(txt[:2500])
            if len(rd.pages) > 1:
                print("--- page 2 text (first 1200 chars) ---")
                print((rd.pages[1].extract_text() or "")[:1200])
        except Exception as e:
            print("pypdf failed:", e)


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
    links = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e => [(e.innerText||'').trim().slice(0,70), e.href])")
    print(f"--- links ({len(links)}) first 60 ---")
    for t, h in links[:60]:
        print(f"  {t!r} -> {h}")
    fields = await page.eval_on_selector_all(
        "input, select, textarea, button",
        """els => els.map(e => [e.tagName, e.type||'', e.name||'', e.id||'', (e.placeholder||e.value||e.innerText||'').trim().slice(0,50),
            e.tagName==='SELECT' ? Array.from(e.options).slice(0,12).map(o=>o.text).join('|') : ''])""")
    print(f"--- form fields ({len(fields)}) ---")
    for f in fields[:60]:
        print("  ", f)


async def main():
    step0()
    step4()
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
                entry = f"[{resp.status}] {req.method} {req.resource_type} {resp.url[:200]}  ({ct[:40]})"
                body = ""
                if req.resource_type in ("xhr", "fetch") or "json" in ct:
                    try:
                        body = (await resp.text())[:700]
                    except Exception:
                        body = "(unreadable)"
                    if req.post_data:
                        entry += f"\n      POST DATA: {req.post_data[:400]}"
                seen.append((entry, body))
            except Exception:
                pass

        page.on("response", on_resp)

        hr("1. Start page")
        try:
            await page.goto(START, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print("goto problem:", e)
        await dump_page(page, "1. START PAGE")

        # Try a simple search: keyword box, a recent-looking reference prefix
        hr("2. Search attempt")
        searched = False
        for sel in ["input[type=search]", "input[name*=eyword i]", "input[id*=eyword i]",
                    "input[name*=earch i]", "input[id*=earch i]", "input[type=text]"]:
            try:
                el = page.locator(sel).first
                if await el.count() and await el.is_visible():
                    print("using input:", sel)
                    await el.fill("WA/2026")
                    await el.press("Enter")
                    searched = True
                    break
            except Exception as e:
                print("input try failed", sel, e)
        if not searched:
            for name in ["Search", "Find", "Go"]:
                try:
                    b = page.get_by_role("button", name=re.compile(name, re.I)).first
                    if await b.count():
                        await b.click()
                        searched = True
                        break
                except Exception:
                    pass
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        await dump_page(page, "2. AFTER SEARCH", 5000)

        # Open first result-looking link (WA/ reference) for a detail page
        hr("3. Detail page")
        try:
            link = page.locator("a", has_text=re.compile(r"WA/\d{4}/\d+")).first
            if await link.count():
                print("opening:", (await link.inner_text())[:80])
                await link.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass
                await page.wait_for_timeout(2500)
                await dump_page(page, "3. DETAIL PAGE", 6000)
            else:
                print("no WA/yyyy/nnnn link found on results page")
        except Exception as e:
            print("detail step failed:", e)

        hr("NETWORK LOG (non-static requests)")
        for entry, body in seen[:120]:
            print(entry)
            if body:
                print("      BODY:", re.sub(r"\s+", " ", body)[:600])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
