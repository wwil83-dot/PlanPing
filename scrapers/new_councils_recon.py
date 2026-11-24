#!/usr/bin/env python3
"""
PlanFind - recon for four candidate councils (2026-11-24). Reads only, writes nothing to the database.
  1. City of Lincoln   (Idox weekly list)            planning.lincoln.gov.uk
  2. Rotherham         (classic ASP weekly list)     planning.rotherham.gov.uk
  3. Barnsley          (Planning Explorer)           planningexplorer.barnsley.gov.uk
  4. North Lincolnshire (custom portal, maybe API)   apps.northlincs.gov.uk
Prints what each page returns so we can decide which to build. Env: ONLY (1-4, optional).
"""
import asyncio, os, re, json
from datetime import date, timedelta
from playwright.async_api import async_playwright

ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CTX = {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
       "viewport": {"width": 1400, "height": 1000}, "locale": "en-GB", "ignore_https_errors": True}
ONLY = os.environ.get("ONLY", "").strip()


def short(t, n=900):
    return re.sub(r"\s+", " ", t or "")[:n]


async def save(page, slug):
    try:
        open(f"/tmp/newrecon_{slug}.html", "w", encoding="utf-8").write(await page.content())
        await page.screenshot(path=f"/tmp/newrecon_{slug}.png", full_page=True)
    except Exception as e:
        print("  (evidence save failed)", e)


async def fields(page):
    out = await page.evaluate("""() => [...document.querySelectorAll('input,select,textarea,button')].map(e =>
        [e.tagName, e.type||'', e.name||'', e.id||'', (e.value||'').slice(0,30), e.disabled?'disabled':''].join('|'))""")
    for f in out[:60]:
        print("    field:", f)


async def lincoln(b):
    print("\n=== 1. CITY OF LINCOLN (Idox weekly list)")
    p = await (await b.new_context(**CTX)).new_page()
    r = await p.goto("https://planning.lincoln.gov.uk/online-applications/search.do?action=weeklyList", wait_until="domcontentloaded", timeout=60000)
    print("  HTTP", r.status if r else "?", "title:", await p.title())
    await fields(p)
    try:
        await p.locator("input[type=submit],button[type=submit]").first.click()
        await p.wait_for_load_state("domcontentloaded")
    except Exception as e:
        print("  submit failed:", str(e)[:120])
    print("  URL after:", p.url)
    txt = await p.inner_text("body")
    print("  text:", short(txt, 700))
    items = await p.locator("li.searchresult").count()
    print("  result items:", items)
    if items:
        print("  first item:", short(await p.locator("li.searchresult").first.inner_text(), 500))
    await save(p, "lincoln")


async def rotherham(b):
    print("\n=== 2. ROTHERHAM (ASP weekly list)")
    p = await (await b.new_context(**CTX)).new_page()
    r = await p.goto("https://planning.rotherham.gov.uk/weeklylistapp.asp", wait_until="domcontentloaded", timeout=60000)
    print("  HTTP", r.status if r else "?", "title:", await p.title())
    await fields(p)
    try:
        sel = p.locator("select[name='wListSort1']")
        if await sel.count():
            opts = await sel.first.evaluate("e => [...e.options].map(o => o.value + '=' + o.text)")
            print("  wListSort1 options:", opts)
            await sel.first.select_option(index=1)
        upd = p.locator("input[value='Update'], input[type=submit]").first
        await upd.click()
        await p.wait_for_load_state("domcontentloaded")
    except Exception as e:
        print("  sort/submit failed:", str(e)[:120])
    txt = await p.inner_text("body")
    print("  text:", short(txt, 1200))
    print("  dates seen:", sorted(set(re.findall(r"\b\d{1,2}[/ ][A-Za-z0-9]{2,9}[/ ]\d{4}\b", txt)))[:15])
    links = await p.evaluate("() => [...document.querySelectorAll('a')].map(a => a.getAttribute('href')).filter(h => h && /detail|app|ref/i.test(h)).slice(0,5)")
    print("  sample detail links:", links)
    await save(p, "rotherham")


async def barnsley(b):
    print("\n=== 3. BARNSLEY (Planning Explorer)")
    p = await (await b.new_context(**CTX)).new_page()
    r = await p.goto("https://planningexplorer.barnsley.gov.uk/Home/AdvanceSearch", wait_until="domcontentloaded", timeout=60000)
    print("  HTTP", r.status if r else "?", "title:", await p.title())
    try:
        await p.get_by_text("Search by dates").first.click()
        await p.wait_for_timeout(800)
    except Exception as e:
        print("  could not open dates panel:", str(e)[:100])
    await fields(p)
    d1, d0 = date.today(), date.today() - timedelta(days=7)
    for fmt in ("%d/%m/%Y",):
        for name in ("DateReceivedFrom", "ReceivedFrom", "ValidFrom", "DateFrom"):
            loc = p.locator(f"input[name*='{name}' i], input[id*='{name}' i]")
            if await loc.count():
                try:
                    await loc.first.fill(d0.strftime(fmt))
                    print("  filled", name)
                except Exception:
                    pass
        for name in ("DateReceivedTo", "ReceivedTo", "ValidTo", "DateTo"):
            loc = p.locator(f"input[name*='{name}' i], input[id*='{name}' i]")
            if await loc.count():
                try:
                    await loc.first.fill(d1.strftime(fmt))
                    print("  filled", name)
                except Exception:
                    pass
    try:
        await p.get_by_role("button", name=re.compile("search", re.I)).first.click()
        await p.wait_for_load_state("domcontentloaded")
    except Exception as e:
        print("  submit failed:", str(e)[:120])
    print("  URL after:", p.url)
    print("  text:", short(await p.inner_text("body"), 1200))
    await save(p, "barnsley")


async def northlincs(b):
    print("\n=== 4. NORTH LINCOLNSHIRE (apps.northlincs.gov.uk)")
    ctx = await b.new_context(**CTX)
    p = await ctx.new_page()
    seen = []

    async def on_resp(resp):
        try:
            ct = resp.headers.get("content-type", "")
            if resp.request.resource_type in ("xhr", "fetch") or "json" in ct:
                body = (await resp.text())[:300]
                seen.append((resp.status, resp.request.method, resp.url, short(body, 300)))
        except Exception:
            pass
    p.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
    r = await p.goto("https://apps.northlincs.gov.uk", wait_until="domcontentloaded", timeout=60000)
    print("  HTTP", r.status if r else "?", "title:", await p.title())
    await p.wait_for_timeout(6000)
    txt = await p.inner_text("body")
    print("  text:", short(txt, 600))
    print("  challenge words:", re.findall(r"captcha|verify you are human|checking your browser|cloudflare|turnstile|recaptcha", txt + await p.content(), re.I)[:5])
    try:
        await p.get_by_text("New this week").first.click()
        await p.wait_for_timeout(4000)
        print("  after 'New this week':", short(await p.inner_text("body"), 500))
    except Exception as e:
        print("  'New this week' click failed:", str(e)[:100])
    print("  XHR/fetch calls seen:")
    for s in seen[:15]:
        print("   ", s)
    await save(p, "northlincs")


async def main():
    jobs = [("1", lincoln), ("2", rotherham), ("3", barnsley), ("4", northlincs)]
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=ARGS)
        for k, fn in jobs:
            if ONLY and k not in ONLY:
                continue
            try:
                await fn(b)
            except Exception as e:
                print("  ✗", fn.__name__, "error:", str(e)[:200])
        await b.close()

asyncio.run(main())
