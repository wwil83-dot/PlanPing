#!/usr/bin/env python3
"""
PlanFind - Wokingham PROBE round 2, through headless Chromium (writes NOTHING to the database).

Round 1 (stdlib) got 'Connection reset by peer' on every planning.wokingham.gov.uk request, while
Chromium in the first recon was served normally -> the host rejects non-browser TLS clients.
So this probe does everything inside Chromium (page.goto / clicks), logging:
  A. advanced search.asp: every form, field, select option (esp. date ranges)
  B. the two weekly-list pages (text, links, fields)
  C. quick search 'KeywordSearch=2026': block count per page, RecCount, Page select, paging to page 2
  D. a detail page opened by CLICKING 'View Details' (direct goto gave HTTP 500), and by direct goto
  E. a best-guess date-range search from the advanced form
"""
import asyncio
import datetime as dt
import re

B = "https://planning.wokingham.gov.uk/FastWebPL/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception as e:
        return f"(no body: {e})"


def refs(txt):
    return re.findall(r"App\. No\.:\s*(\S+)", txt)


async def dump_forms(page, label):
    forms = await page.evaluate("""() => Array.from(document.forms).map(f => ({
        name: f.name, id: f.id, action: f.action, method: f.method,
        fields: Array.from(f.elements).map(e => ({tag: e.tagName, type: e.type||'', name: e.name||'', id: e.id||'',
            value: (e.value||'').slice(0,40),
            options: e.tagName==='SELECT' ? Array.from(e.options).slice(0,14).map(o=>o.value+'='+o.text.slice(0,24)) : null}))
    }))""")
    print(f"--- {label}: {len(forms)} forms ---")
    for f in forms:
        print(f" FORM name={f['name']!r} id={f['id']!r} method={f['method']} action={f['action']}")
        for e in f["fields"]:
            line = f"    {e['tag']:<8} {e['type']:<9} name={e['name']!r:<26} id={e['id']!r:<22} value={e['value']!r}"
            print(line)
            if e["options"]:
                print("       options:", e["options"])
    return forms


async def main():
    from playwright.async_api import async_playwright
    today = dt.date.today()
    d1, d2 = (today - dt.timedelta(days=14)).strftime("%d/%m/%Y"), today.strftime("%d/%m/%Y")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=UA, locale="en-GB", ignore_https_errors=True,
                                        viewport={"width": 1400, "height": 1000})
        page = await ctx.new_page()
        net = []
        page.on("response", lambda r: net.append((r.status, r.request.method, r.url[:160]))
                if "wokingham" in r.url and r.request.resource_type in ("document", "xhr", "fetch") else None)

        hr("A. Advanced search page search.asp")
        await page.goto(B + "welcome.asp", wait_until="networkidle", timeout=60000)
        await page.goto(B + "search.asp", wait_until="networkidle", timeout=60000)
        print("URL", page.url, "| title", await page.title())
        adv = await dump_forms(page, "search.asp")
        print((await body_text(page))[:1200])

        hr("B. Weekly lists")
        for name in ("weeklylistapp.asp", "weeklylistdec.asp"):
            await page.goto(B + name, wait_until="networkidle", timeout=60000)
            print(f"\n--- {name}: {page.url} | {await page.title()} ---")
            await dump_forms(page, name)
            print((await body_text(page))[:1500])
            links = await page.eval_on_selector_all("a[href]", "els => els.map(e => [(e.innerText||'').trim().slice(0,50), e.href])")
            for t, h in links:
                if "wokingham.gov.uk/FastWebPL" in h and not h.endswith("#"):
                    print("   link:", repr(t), "->", h[:150])

        hr("C. Quick search KeywordSearch=2026 and paging")
        await page.goto(B + "welcome.asp", wait_until="networkidle", timeout=60000)
        await page.fill("#KeywordSearch", "2026")
        await page.click("input[name=Submit]")
        await page.wait_for_load_state("networkidle")
        t1 = await body_text(page)
        r1 = refs(t1)
        print("page 1 refs:", len(r1), r1[:12])
        pf = await dump_forms(page, "results page")
        sel = page.locator("select[name=Page]")
        if await sel.count():
            await sel.select_option("2")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            t2 = await body_text(page)
            if refs(t2) == r1:
                print("select did not navigate by itself; submitting its form")
                await page.evaluate("document.querySelector('select[name=Page]').form.submit()")
                await page.wait_for_load_state("networkidle")
                t2 = await body_text(page)
            r2 = refs(t2)
            print("page 2 refs:", len(r2), r2[:12], "| overlap with page 1:", set(r1) & set(r2))

        hr("D. Detail page")
        await page.goto(B + "welcome.asp", wait_until="networkidle")
        await page.fill("#KeywordSearch", "2026")
        await page.click("input[name=Submit]")
        await page.wait_for_load_state("networkidle")
        link = page.get_by_role("link", name="View Details").first
        try:
            await link.click(timeout=10000)
            await page.wait_for_load_state("networkidle")
            print("clicked ->", page.url, "|", await page.title())
            print((await body_text(page))[:2500])
        except Exception as e:
            print("click failed:", e)
        ref = r1[0] if r1 else "262316"
        await page.goto(B + f"detail.asp?AltRef={ref}", wait_until="networkidle")
        print("\ndirect goto AltRef only ->", page.url, "|", await page.title())
        print((await body_text(page))[:300])

        hr("E. Date-range search (best guess from the advanced form)")
        await page.goto(B + "search.asp", wait_until="networkidle")
        inputs = await page.eval_on_selector_all("form input[type=text]", "els => els.map(e => e.name)")
        print("text inputs:", inputs)
        filled = {}
        for n in inputs:
            ln = n.lower()
            if re.search(r"valid|recei", ln) and re.search(r"from|start|1", ln):
                filled[n] = d1
            elif re.search(r"valid|recei", ln) and re.search(r"to|end|2", ln):
                filled[n] = d2
        print("filling:", filled)
        for n, v in filled.items():
            await page.fill(f"input[name={n}]", v)
        if filled:
            btn = page.locator("input[type=submit]").first
            await btn.click()
            await page.wait_for_load_state("networkidle")
            tt = await body_text(page)
            print("result refs:", len(refs(tt)), refs(tt)[:10])
            print(tt[:700])

        hr("NETWORK (documents / xhr)")
        for n in net[:40]:
            print("  ", n)
        await browser.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
