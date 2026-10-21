#!/usr/bin/env python3
"""
PlanFind - Wokingham PROBE round 3, through headless Chromium (writes NOTHING to the database).

Round 2 found the advanced search form (POST results.asp, form 'SearchForm') with the date fields
DateValidStart/End, DateReceivedStart/End, DateDecidedStart/End (dd/mm/yyyy), ShowDecided
(2=Decided, 1=Not Decided), DecisionDescription, Sort1/Sort2. The results page shows ~20 hits per
page with ref, address, description, received/valid/decision dates and decision text, but its
'Page' select sits OUTSIDE a form, so my submit failed. This round learns:
  A. a Date Valid window search: total (RecCount), refs per page, and exactly how the paging control
     works (HTML around select[name=Page], inline JS, links)
  B. getting page 2/3 by every route (change event, JS, links), and whether pages overlap
  C. a Date Decided window search
  D. a single-day search (fallback if paging cannot be driven)
  E. the full DecisionDescription vocabulary (for status mapping)
  F. one detail page opened by clicking 'View Details'
Every section is wrapped so one failure does not stop the rest.
"""
import asyncio
import datetime as dt
import re

B = "https://planning.wokingham.gov.uk/FastWebPL/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


async def txt(page):
    try:
        return await page.inner_text("body")
    except Exception as e:
        return f"(no body: {e})"


def refs(t):
    return re.findall(r"App\. No\.:\s*(\S+)", t)


async def adv_search(page, **fields):
    await page.goto(B + "welcome.asp", wait_until="networkidle", timeout=60000)
    await page.goto(B + "search.asp", wait_until="networkidle", timeout=60000)
    for k, v in fields.items():
        if k == "ShowDecided":
            await page.select_option(f"select[name={k}]", v)
        else:
            await page.fill(f"input[name={k}]", v)
    await page.click("input#Submit")
    await page.wait_for_load_state("networkidle")


async def paging_info(page):
    rc = await page.evaluate("() => { const e = document.querySelector('input[name=RecCount]'); return e ? e.value : null }")
    sel = await page.evaluate("""() => { const s = document.querySelector('select[name=Page]');
        return s ? {outer: s.outerHTML.slice(0,400), form: !!s.form, parent: s.parentElement.outerHTML.slice(0,900),
                    grand: s.parentElement.parentElement.outerHTML.slice(0,1400)} : null }""")
    return rc, sel


async def safe(label, coro):
    try:
        await coro
    except Exception as e:
        print(f"!! {label} failed: {type(e).__name__}: {str(e)[:300]}")


async def main():
    from playwright.async_api import async_playwright
    today = dt.date.today()
    f = lambda d: d.strftime("%d/%m/%Y")
    d1, d2 = f(today - dt.timedelta(days=14)), f(today)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=UA, locale="en-GB", ignore_https_errors=True,
                                        viewport={"width": 1400, "height": 1000})
        page = await ctx.new_page()
        net = []
        page.on("response", lambda r: net.append((r.status, r.request.method, r.url[:150], (r.request.post_data or "")[:150]))
                if "FastWebPL" in r.url and r.request.resource_type in ("document", "xhr", "fetch") else None)

        async def a_b():
            hr(f"A. Date VALID window {d1} .. {d2}")
            await adv_search(page, DateValidStart=d1, DateValidEnd=d2)
            t1 = await txt(page)
            r1 = refs(t1)
            rc, sel = await paging_info(page)
            print("URL", page.url, "| RecCount", rc, "| refs on page 1:", len(r1), r1[:8])
            print(re.sub(r"\n{2,}", "\n", t1)[:600])
            print("select[name=Page]:", sel)
            scripts = await page.evaluate("""() => Array.from(document.scripts).map(s => s.text).filter(t => /Page|submit|results\\.asp/i.test(t)).map(t => t.slice(0,900))""")
            print("inline scripts mentioning Page/submit:", scripts[:3])
            nav = await page.eval_on_selector_all("a[href]", """els => els.filter(e => /next|prev|page|>>|»|›/i.test((e.innerText||'')+e.href)).map(e => [(e.innerText||'').trim().slice(0,30), e.href.slice(0,140)])""")
            print("paging-looking links:", nav[:10])

            hr("B. Getting page 2")
            if sel:
                loc = page.locator("select[name=Page]")
                opts = await page.eval_on_selector("select[name=Page]", "s => Array.from(s.options).map(o => o.value)")
                print("Page options:", opts)
                await loc.select_option("2")
                await page.evaluate("document.querySelector('select[name=Page]').dispatchEvent(new Event('change', {bubbles:true}))")
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                t2 = await txt(page)
                r2 = refs(t2)
                print("after change event: URL", page.url, "| refs", len(r2), r2[:8], "| same as page 1:", r2 == r1,
                      "| overlap:", len(set(r1) & set(r2)))
                if r2 == r1:
                    print("page unchanged; page HTML around the select now:", (await paging_info(page))[1])
            print("network so far:")
            for n in net[-8:]:
                print("  ", n)
        await safe("A/B", a_b())

        async def c():
            hr(f"C. Date DECIDED window {d1} .. {d2}")
            await adv_search(page, DateDecidedStart=d1, DateDecidedEnd=d2)
            t = await txt(page)
            rc, _ = await paging_info(page)
            print("RecCount", rc, "| refs", len(refs(t)), refs(t)[:6])
            i = t.find("App. No.:")
            print(re.sub(r"\n{2,}", "\n", t[i:i + 900]))
        await safe("C", c())

        async def d():
            day = f(today - dt.timedelta(days=3))
            hr(f"D. Single-day Date VALID search {day}")
            await adv_search(page, DateValidStart=day, DateValidEnd=day)
            t = await txt(page)
            rc, _ = await paging_info(page)
            print("RecCount", rc, "| refs", len(refs(t)), refs(t)[:20])
            day2 = f(today - dt.timedelta(days=3))
            await adv_search(page, DateDecidedStart=day2, DateDecidedEnd=day2)
            t = await txt(page)
            rc, _ = await paging_info(page)
            print("decided that day: RecCount", rc, "| refs", len(refs(t)))
        await safe("D", d())

        async def e():
            hr("E. DecisionDescription vocabulary and ShowDecided")
            await page.goto(B + "search.asp", wait_until="networkidle")
            for name in ("DecisionDescription", "ShowDecided"):
                opts = await page.eval_on_selector(f"select[name={name}]", "s => Array.from(s.options).map(o => o.value.trim())")
                print(name, len(opts), opts)
        await safe("E", e())

        async def fdetail():
            hr("F. Detail page via click")
            await page.goto(B + "welcome.asp", wait_until="networkidle")
            await page.fill("#KeywordSearch", "2026")
            await page.click("input[name=Submit]")
            await page.wait_for_load_state("networkidle")
            await page.get_by_role("link", name="View Details").first.click(timeout=10000)
            await page.wait_for_load_state("networkidle")
            print("URL", page.url, "|", await page.title())
            print((await txt(page))[:2500])
        await safe("F", fdetail())

        await browser.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
