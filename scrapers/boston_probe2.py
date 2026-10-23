#!/usr/bin/env python3
"""
PlanFind - Boston recon round 2 (writes NOTHING).

Round 1 showed Boston's new applications ARE on publicaccess.e-lindsey.gov.uk, mixed in with East Lindsey's
(same reference pattern, e.g. 01885/26/ABC at 'The Plantation Rowdyke Road Wyberton Boston PE21 7AQ'),
and found by a date-only search - not by the old Boston wards. So the ward filter is why boston_scraper
sees nothing. This works out the best way to pick Boston's applications out of the shared stream:
  A. a detail page of a Boston-looking application, Summary AND Further Information tabs: is there a
     council / ward / parish / district field that identifies Boston?
  B. parish search: the parish list has 'Boston' (404) and other 4xx parish codes (Boston borough?);
     do date + parish searches return these new applications?
  C. a 30-day date-only sweep with paging: how many pages, how many rows, how many look like Boston by
     address (PE20/PE21/PE22 or the word Boston), and a sample of the rest, to judge false positives.
Send the whole log back.
"""
import asyncio
import re
from datetime import date, timedelta
from urllib.parse import urljoin

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE = "https://publicaccess.e-lindsey.gov.uk/online-applications"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
CTX = {"user_agent": UA, "viewport": {"width": 1280, "height": 900}, "locale": "en-GB", "ignore_https_errors": True}
BOSTON = re.compile(r"\bPE2[0-2]\b|\bboston\b", re.I)
ITEMS_JS = """els => els.map(e => ({
    href: (e.querySelector('a.summaryLink')||{}).href||'',
    addr: (e.querySelector('p.address')||{}).innerText||'',
    meta: (e.querySelector('p.metaInfo')||{}).innerText||''}))"""


def hr(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8, flush=True)


def one(s):
    return re.sub(r"\s+", " ", s or "").strip()


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass


async def submit_search(page, start, end, parish=None, ward=None):
    await page.goto(f"{BASE}/search.do?action=advanced", wait_until="domcontentloaded", timeout=45000)
    await settle(page)
    if parish:
        await page.locator("select[name='searchCriteria.parish']").select_option(value=parish)
    if ward:
        await page.locator("select[name='searchCriteria.ward']").select_option(value=ward)
    await page.fill("input[name='date(applicationReceivedStart)']", start.strftime("%d/%m/%Y"))
    await page.fill("input[name='date(applicationReceivedEnd)']", end.strftime("%d/%m/%Y"))
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        await page.locator("input[type='submit'], button[type='submit']").first.click()
    await settle(page)


async def read_all_pages(page, max_pages=15):
    rows, pages = [], 0
    while pages < max_pages:
        pages += 1
        rows += await page.eval_on_selector_all("li.searchresult", ITEMS_JS)
        nxt = page.locator("a.next")
        if await nxt.count() == 0:
            break
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await nxt.first.click()
            await settle(page)
        except Exception as e:
            print(f"   paging stopped on page {pages}: {type(e).__name__}")
            break
        await asyncio.sleep(1)
    return rows, pages


async def main():
    today = date.today()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(**CTX)
        page = await ctx.new_page()

        hr("C. 30-day date-only sweep with paging")
        await submit_search(page, today - timedelta(days=30), today)
        body = one(await page.inner_text("body"))
        m = re.search(r"Showing[^.]{0,60}|\d+\s+results?[^.]{0,40}|Too many[^.]*\.|No results[^.]*\.", body, re.I)
        print("count line:", m.group(0) if m else None)
        rows, pages = await read_all_pages(page)
        print(f"pages read: {pages}   rows: {len(rows)}")
        b = [r for r in rows if BOSTON.search(r["addr"])]
        print(f"Boston-looking by address: {len(b)} of {len(rows)}")
        print("sample Boston-looking:")
        for r in b[:10]:
            print("   ", one(r["meta"])[:70], "|", one(r["addr"])[:80])
        print("sample NOT Boston-looking:")
        for r in [r for r in rows if not BOSTON.search(r["addr"])][:8]:
            print("   ", one(r["meta"])[:70], "|", one(r["addr"])[:80])
        refs = [re.search(r"Ref\. No:\s*(\S+)", r["meta"]) for r in rows]
        shapes = sorted({re.sub(r"\d", "9", m.group(1)) for m in refs if m})
        print("reference shapes:", shapes[:20])

        hr("A. Detail page of a Boston-looking application")
        target = next((r for r in b if r["href"]), None)
        if target:
            print("detail url:", target["href"])
            for tab in ("summary", "details", "contacts"):
                url = re.sub(r"activeTab=\w+", f"activeTab={tab}", target["href"])
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await settle(page)
                    print(f"\n--- tab {tab} ---")
                    print(one(await page.inner_text("#pa, main, body"))[:1800])
                except Exception as e:
                    print(f"tab {tab} failed: {type(e).__name__}: {e}")
        else:
            print("no Boston-looking row with a link found")

        hr("B. Parish searches (30 days) for Boston-looking parish codes")
        await page.goto(f"{BASE}/search.do?action=advanced", wait_until="domcontentloaded", timeout=45000)
        await settle(page)
        opts = await page.eval_on_selector_all(
            "select[name='searchCriteria.parish'] option", "els => els.map(o => [o.value, o.text.trim()])")
        cand = [(v, t) for v, t in opts if v.startswith("4")]
        print(f"parish options starting with 4 (likely Boston borough): {len(cand)}")
        print(cand)
        for v, t in cand[:45]:
            try:
                await submit_search(page, today - timedelta(days=30), today, parish=v)
                n = await page.locator("li.searchresult").count()
                more = await page.locator("a.next").count()
                print(f"   parish {v} {t!r}: {n} rows on page 1{' (+ more pages)' if more else ''}")
            except Exception as e:
                print(f"   parish {v} {t!r} failed: {type(e).__name__}")
            await asyncio.sleep(1)
        await browser.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
