#!/usr/bin/env python3
"""PlanFind - Elmbridge Borough Council (emaps.elmbridge.gov.uk, bespoke ebc_planning.aspx) reconnaissance, 2026-11-16.
Opens the Advanced Search tab, lists every form field, runs one search (validated in the last 14 days) and reports the results page:
tables, rows, links, pagination, and the first rows' text. HTML saved to /tmp/elmbridge_probe_*.html (uploaded as an artifact)."""
import asyncio, re
from datetime import date, timedelta
from playwright.async_api import async_playwright

URL = "https://emaps.elmbridge.gov.uk/ebc_planning.aspx?requesttype=parseTemplate&template=AdvancedSearchTab.tmplt&pagerecs=2000#advanced"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        ctx = await b.new_context(locale="en-GB", ignore_https_errors=True, viewport={"width": 1280, "height": 900})
        pg = await ctx.new_page()
        r = await pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("HTTP", r.status if r else None, "->", pg.url)
        await pg.wait_for_timeout(2500)
        open("/tmp/elmbridge_probe_form.html", "w").write(await pg.content())
        print("\n=== FORM FIELDS ===")
        for el in await pg.query_selector_all("form input, form select, form textarea, form button"):
            d = await el.evaluate("e=>({tag:e.tagName,type:e.type,name:e.name,id:e.id,val:(e.value||'').slice(0,30),opts:e.tagName=='SELECT'?[...e.options].slice(0,6).map(o=>o.text):null})")
            print("  ", d)
        today = date.today()
        a = (today - timedelta(days=14)).isoformat()
        frm = None
        for sel_name in ("Date Validated from", "Date Validated"):
            pass
        dates = await pg.query_selector_all("input[type=date]")
        print("\ndate inputs:", len(dates))
        if len(dates) >= 2:
            await dates[0].fill(a)
            await dates[1].fill(today.isoformat())
        btn = await pg.query_selector("input[type=submit][value=Search], button:has-text('Search'), input[value=Search]")
        print("search button:", bool(btn))
        if btn:
            await btn.click()
            await pg.wait_for_load_state("domcontentloaded", timeout=60000)
            await pg.wait_for_timeout(3000)
        print("\nRESULTS URL:", pg.url)
        html = await pg.content()
        open("/tmp/elmbridge_probe_results.html", "w").write(html)
        print("html length", len(html))
        for t in await pg.query_selector_all("table"):
            rows = await t.query_selector_all("tr")
            print("TABLE rows:", len(rows), "id/class:", await t.get_attribute("id"), await t.get_attribute("class"))
        rows = await pg.query_selector_all("table tr")
        for tr in rows[:8]:
            print("   ROW:", " | ".join((await tr.inner_text()).split())[:250])
        links = await pg.query_selector_all("a[href*='ref'], a[href*='Ref'], a[href*='ebc_planning']")
        print("candidate links:", len(links))
        for l in links[:6]:
            print("   ", (await l.inner_text())[:40], "->", await l.get_attribute("href"))
        txt = " ".join((await pg.inner_text("body")).split())
        for m in re.finditer(r"(\d+ (records?|results?|applications?) found|Page \d+ of \d+|Showing[^.]{0,60})", txt, re.I):
            print("count/paging text:", m.group(0))
        await b.close()

asyncio.run(main())
