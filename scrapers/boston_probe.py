#!/usr/bin/env python3
"""
PlanFind - Boston recon after the council's move to the new shared planning system (writes NOTHING).

boston_scraper.py (ward-based, publicaccess.e-lindsey.gov.uk) now finds 0 applications in all 15 wards.
Boston's notice says the Planning Service moved to a new system in the week of 14 Sep 2026 (new
reference format) and that the old register redirects to the new platform. Questions this answers:
  A. Is the advanced form the same? Ward options (is anything Boston-like there now?), other fields.
  B. Date-only searches (no ward): do results come back, how many, and do any look like Boston
     (PE20/PE21 postcodes, 'Boston' in address)? What do the new references look like?
  C. The old Boston ward search for the last 30 days: what does the page actually say?
  D. Does the Boston website still serve a weekly list / does it point to a new host?
Send the whole log back.
"""
import asyncio
import re
from datetime import date, timedelta

import httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE = "https://publicaccess.e-lindsey.gov.uk/online-applications"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
CTX = {"user_agent": UA, "viewport": {"width": 1280, "height": 900}, "locale": "en-GB", "ignore_https_errors": True}
BOSTON_PC = re.compile(r"\bPE2[01]\b|\bPE22\b|boston", re.I)


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass


async def results_summary(page, label):
    txt = re.sub(r"\s+", " ", await page.inner_text("body"))
    print(f"[{label}] url: {page.url}")
    m = re.search(r"(\d[\d,]*)\s+results?\s+found|Showing\s+\d+\s*-\s*\d+\s+of\s+\d+|No results found|Too many results[^.]*\.", txt, re.I)
    print(f"[{label}] count line: {m.group(0) if m else None}")
    items = await page.eval_on_selector_all(
        "li.searchresult",
        """els => els.map(e => ({
            addr: (e.querySelector('p.address')||{}).innerText||'',
            meta: (e.querySelector('p.metaInfo')||{}).innerText||'',
            status: (e.querySelector('.badge-status .value')||{}).innerText||''}))""")
    print(f"[{label}] li.searchresult items on page: {len(items)}")
    boston = 0
    for it in items:
        flag = bool(BOSTON_PC.search(it["addr"]))
        boston += flag
    for it in items[:8]:
        print("   ", re.sub(r"\s+", " ", it["meta"])[:90], "|", re.sub(r"\s+", " ", it["addr"])[:90], "|", it["status"],
              "<-- BOSTON?" if BOSTON_PC.search(it["addr"]) else "")
    print(f"[{label}] Boston-looking on this page: {boston} of {len(items)}")
    if not items:
        print(f"[{label}] page text head: {txt[:700]}")
    return items


async def search(page, label, start, end, ward=None):
    await page.goto(f"{BASE}/search.do?action=advanced", wait_until="domcontentloaded", timeout=45000)
    await settle(page)
    if ward:
        await page.locator("select[name='searchCriteria.ward']").select_option(value=ward)
    await page.fill("input[name='date(applicationReceivedStart)']", start.strftime("%d/%m/%Y"))
    await page.fill("input[name='date(applicationReceivedEnd)']", end.strftime("%d/%m/%Y"))
    sub = page.locator("input[type='submit'], button[type='submit']")
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        await sub.first.click()
    await settle(page)
    return await results_summary(page, label)


async def main():
    today = date.today()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(**CTX)
        page = await ctx.new_page()

        hr("A. Advanced search form")
        await page.goto(f"{BASE}/search.do?action=advanced", wait_until="domcontentloaded", timeout=45000)
        await settle(page)
        sels = await page.eval_on_selector_all("select", """els => els.map(s => ({name: s.name,
            opts: Array.from(s.options).map(o => o.value + '=' + o.text.trim())}))""")
        for s in sels:
            print(f"select {s['name']!r}: {len(s['opts'])} options")
            if s["name"] in ("searchCriteria.ward", "searchCriteria.parish") or len(s["opts"]) < 25:
                print("   ", s["opts"][:80])
        inputs = await page.eval_on_selector_all("input", "els => els.map(i => i.name + ':' + i.type)")
        print("inputs:", [i for i in inputs if i][:60])
        body = re.sub(r"\s+", " ", await page.inner_text("body"))
        for m in re.finditer(r"[^.]*\b(new (planning )?(system|platform)|Boston|migrat|moved)[^.]*\.", body, re.I):
            print("notice:", m.group(0).strip()[:300])

        hr("B. Date-only searches (no ward)")
        for days in (7, 3):
            try:
                await search(page, f"all councils, last {days}d", today - timedelta(days=days), today)
            except Exception as e:
                print(f"last {days}d failed: {type(e).__name__}: {e}")

        hr("C. Old Boston ward searches (last 30 days)")
        for code in ("KIFR", "STAT", "WEST"):
            try:
                await search(page, f"ward {code}", today - timedelta(days=30), today, ward=code)
            except Exception as e:
                print(f"ward {code} failed: {type(e).__name__}: {e}")

        hr("D. Simple search for 'Boston' text")
        try:
            await page.goto(f"{BASE}/search.do?action=simple&searchType=Application", wait_until="domcontentloaded", timeout=45000)
            await settle(page)
            box = page.locator("input[name='searchCriteria.simpleSearchString']")
            await box.fill("PE21")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await page.locator("input[type='submit'], button[type='submit']").first.click()
            await settle(page)
            await results_summary(page, "simple 'PE21'")
        except Exception as e:
            print(f"simple search failed: {type(e).__name__}: {e}")
        await browser.close()

    hr("E. Boston website (weekly list and notices) over plain HTTP")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": UA}) as c:
        for u in ("https://www.boston.gov.uk/weeklyplanninglist", "https://www.boston.gov.uk/planning",
                  "https://www.boston.gov.uk/article/21253/Information-and-Notices",
                  "https://www.boston.gov.uk/article/30887/Planning-System-Update"):
            try:
                r = await c.get(u)
                t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
                print(f"\n{u} -> {r.status_code} final {r.url} len {len(r.text)}")
                for h in sorted(set(re.findall(r'href="([^"]*(?:planning|publicaccess|idox|apply|register|e-lindsey)[^"]*)"', r.text, re.I)))[:25]:
                    print("   link:", h)
                print("   text:", t[:500])
            except Exception as e:
                print(f"{u} failed: {e}")
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
