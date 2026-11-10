#!/usr/bin/env python3
"""Birmingham probe: run one real monthly search (October 2026, Decided then Validated) and report what the
results page actually contains, because birmingham_scraper.py's parser found no table.OnlinePlanningWeeklyMonthlySort.
Reports: page text head, every table (classes, headers, first rows), repeated card-like blocks, pagination, page-size select.
HTML is saved to /tmp/birmingham_probe_*.html and uploaded as an artifact."""
import asyncio, re, os
from playwright.async_api import async_playwright, TimeoutError as PWT
import birmingham_scraper as B

MONTH = os.environ.get("PROBE_MONTH", "October 2026")


async def load(page):
    for t in (1, 2, 3):
        try:
            await page.goto(B.SEARCH_URL, wait_until="domcontentloaded", timeout=90000)
            break
        except PWT:
            print(f"  goto {t} timed out")
            await asyncio.sleep(10)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PWT:
        pass


async def dump(page, tag):
    html = await page.content()
    open(f"/tmp/birmingham_probe_{tag}.html", "w", encoding="utf-8").write(html)
    info = await page.evaluate("""() => {
      const out = {url: location.href, title: document.title, tables: [], pag: [], selects: [], blocks: []};
      document.querySelectorAll('table').forEach(t => {
        const hs = [...t.querySelectorAll('th')].map(x => x.innerText.trim()).slice(0, 12);
        const rows = [...t.querySelectorAll('tr')];
        out.tables.push({cls: t.className, id: t.id, rows: rows.length, headers: hs,
                         sample: rows.slice(1, 3).map(r => r.innerText.replace(/\\s+/g, ' ').slice(0, 300)),
                         links: [...t.querySelectorAll('a')].slice(0, 3).map(a => a.getAttribute('href'))});
      });
      document.querySelectorAll('ul.pagination, .pagination').forEach(p => out.pag.push(p.innerText.replace(/\\s+/g,' ').slice(0,200)));
      document.querySelectorAll('select').forEach(s => out.selects.push({id: s.id, name: s.name, n: s.options.length}));
      // repeated blocks that mention a planning reference-like token
      const seen = {};
      document.querySelectorAll('li, div, article, tr').forEach(e => {
        if (e.children.length && /\\b\\d{2}\\/\\d{4,5}\\/\\w+/.test(e.innerText || '') && e.innerText.length < 700) {
          const k = e.tagName + '.' + e.className;
          seen[k] = (seen[k] || 0) + 1;
          if (seen[k] === 1) out.blocks.push({key: k, text: e.innerText.replace(/\\s+/g, ' ').slice(0, 300), a: [...e.querySelectorAll('a')].slice(0,2).map(a => a.getAttribute('href'))});
        }
      });
      out.blockCounts = seen;
      return out;
    }""")
    txt = re.sub(r"\s+", " ", await page.inner_text("body"))
    print(f"\n===== {tag} =====")
    print("URL:", info["url"], "| title:", info["title"])
    print("body text head:", txt[:700])
    m = re.findall(r"[^.]{0,60}\b(?:results?|showing|found)\b[^.]{0,80}", txt, flags=re.I)
    print("count-ish text:", m[:4])
    print("tables:", len(info["tables"]))
    for t in info["tables"]:
        print("  table", t)
    print("pagination:", info["pag"])
    print("selects:", info["selects"])
    print("reference-like blocks:", info["blockCounts"])
    for b in info["blocks"][:4]:
        print("  block", b)
    print("parse_results_page ->", len(B._parse_results_page(html)), "rows")


async def netdump(page, reqs, tag):
    print(f"\n----- {tag}: network after clicking Search -----")
    for r in reqs[:25]:
        print("  ", r)
    state = await page.evaluate("""() => {
      const d = document.querySelector('#divOnlinePlanningSearchResults');
      const vis = e => !!(e.offsetWidth || e.offsetHeight);
      return {resultsLen: d ? d.innerHTML.length : -1,
              resultsHead: d ? d.innerText.replace(/\\s+/g,' ').slice(0, 600) : null,
              resultsHtml: d ? d.innerHTML.replace(/\\s+/g,' ').slice(0, 1500) : null,
              loadingVisible: [...document.querySelectorAll('#divLoading')].map(vis),
              errors: [...document.querySelectorAll('.error, .errorRed, .field-validation-error, .validation-summary-errors')].filter(vis).map(e => e.innerText.trim()).filter(Boolean).slice(0, 8),
              month: (document.querySelector('#SelectedMonth')||{}).value,
              status: [...document.querySelectorAll('input.MonthlyListStatus')].map(i => i.id + ':' + i.checked)};
    }""")
    for k, v in state.items():
        print(f"   {k}: {v}")


async def jsdump(page):
    try:
        r = await page.request.get("https://planning.birmingham.gov.uk/NECSWS/ES/Presentation/Scripts/Planning/OnlinePlanningWeeklyMonthlySearch.js")
        js = await r.text()
        open("/tmp/birmingham_probe_WeeklyMonthlySearch.js", "w").write(js)
        i = js.find("SubmitWeeklyMonthlyForm")
        print("\n----- OnlinePlanningWeeklyMonthlySearch.js", r.status, len(js), "bytes; SubmitWeeklyMonthlyForm: -----")
        print(js[i:i + 2500] if i >= 0 else js[:1500])
    except Exception as e:
        print("js fetch failed", type(e).__name__, e)


async def run(page, status_id, tag):
    await load(page)
    for t in (1, 2, 3):
        try:
            await B.open_monthly_panel(page)
            if await page.locator("select#SelectedMonth").count():
                break
        except Exception as e:
            print(f"  panel try {t}: {type(e).__name__}")
        await load(page)
    sel = page.locator("select#SelectedMonth")
    if not await sel.count():
        print(tag, "no month select")
        await dump(page, tag + "_nopanel")
        return
    await sel.select_option(label=MONTH)
    await page.locator(f"input#{status_id}").check(force=True)
    btn = await B.find_real_search_button(page)
    print(tag, "search button found:", btn is not None)
    if btn is None:
        await dump(page, tag + "_nobtn")
        return
    reqs = []
    page.on("response", lambda r: reqs.append(f"{r.request.method} {r.status} {r.url[-110:]}") if "planning.birmingham" in r.url and not re.search(r"\.(png|gif|css|js|woff2?|ico)(\?|$)", r.url) else None)
    await btn.click()
    for i in range(18):                      # up to 90 s for the AJAX results to arrive
        await asyncio.sleep(5)
        n = await page.evaluate("() => { const d = document.querySelector('#divOnlinePlanningSearchResults'); return d ? d.innerHTML.length : -1 }")
        if n > 50:
            print(f"  results div filled after ~{(i + 1) * 5}s ({n} chars)")
            break
    await netdump(page, reqs, tag)
    await dump(page, tag)


async def main():
    async with async_playwright() as pw:
        br = await pw.chromium.launch(headless=True, args=getattr(B, "BROWSER_ARGS", []) or [])
        ctx = await br.new_context()
        page = await ctx.new_page()
        await jsdump(page)
        await run(page, "DecidedThisMonth", "decided")
        await run(page, "ValidatedThisMonth", "validated")
        await br.close()

asyncio.run(main())
