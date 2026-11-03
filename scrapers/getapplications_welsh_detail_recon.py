"""Recon 2 (no writes): how can Gwynedd / Ceredigion application detail pages be read?
Variants per application, all after first opening the council's search page (to pass any challenge):
  A) fetch() GET from inside the page      B) fetch() POST (fa=getApplication&id=N) from inside the page
  C) navigating the page to the detail URL (cookies from the search page already set)"""
import asyncio, re
import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from getapplications_scraper import _parse_detail_page, _extract_id_from_url, CONTEXT_OPTIONS, BROWSER_ARGS, SUPABASE_URL, SUPABASE_KEY

BASES = {574: "https://amg.gwynedd.llyw.cymru", 575: "https://ceredigion-online.tascomi.com"}


async def oldest_pending(cid, n=2):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/planning_applications",
                        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                        params={"select": "reference,council_url,submitted_date", "council_id": f"eq.{cid}", "status": "eq.pending",
                                "order": "submitted_date.asc", "limit": str(n)})
        return r.json()


def show(tag, status, html):
    soup = BeautifulSoup(html or "", "html.parser")
    text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
    fields = _parse_detail_page(html or "")
    print(f"  [{tag}] HTTP {status}; {len(html or '')} chars; title {soup.title.get_text(strip=True) if soup.title else None!r}")
    print(f"      parser fields: { {k: v[:40] for k, v in fields.items()} }")
    print(f"      Decision: {fields.get('Decision')!r}  issued: {fields.get('Decision Issued Date')!r}")
    print("      text: " + text[:700].replace("\n", " | "))


FETCH_GET = """async (u) => { const r = await fetch(u, {credentials: 'same-origin'}); return {status: r.status, text: await r.text()}; }"""
FETCH_POST = """async ({u, body}) => { const r = await fetch(u, {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                 body: body, credentials: 'same-origin'}); return {status: r.status, text: await r.text()}; }"""


async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        for cid, base in BASES.items():
            rows = await oldest_pending(cid)
            print(f"\n######## council {cid} {base}: {len(rows)} applications")
            ctx = await b.new_context(**CONTEXT_OPTIONS)
            page = await ctx.new_page()
            r0 = await page.goto(f"{base}/planning/index.html?fa=getApplications", wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            print(f"search page: HTTP {r0.status if r0 else None}, title {await page.title()!r}")
            await page.wait_for_timeout(3000)
            for row in rows:
                app_id = _extract_id_from_url(row.get("council_url") or "")
                url = f"{base}/planning/index.html?fa=getApplication&id={app_id}"
                print(f"\n=== {row['reference']} (submitted {row['submitted_date']}) id={app_id}")
                try:
                    res = await page.evaluate(FETCH_GET, url)
                    show("A fetch GET", res["status"], res["text"])
                except Exception as e:
                    print("  [A] failed:", type(e).__name__, str(e)[:100])
                await asyncio.sleep(5)
                try:
                    res = await page.evaluate(FETCH_POST, {"u": f"{base}/planning/index.html", "body": f"fa=getApplication&id={app_id}"})
                    show("B fetch POST", res["status"], res["text"])
                except Exception as e:
                    print("  [B] failed:", type(e).__name__, str(e)[:100])
                await asyncio.sleep(5)
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    show("C navigate", resp.status if resp else None, await page.content())
                except Exception as e:
                    print("  [C] failed:", type(e).__name__, str(e)[:100])
                await asyncio.sleep(5)
                if page.url != f"{base}/planning/index.html?fa=getApplications":   # back to the search page for the next one
                    try:
                        await page.goto(f"{base}/planning/index.html?fa=getApplications", wait_until="domcontentloaded", timeout=45000)
                    except Exception:
                        pass
            await ctx.close()
        await b.close()

asyncio.run(main())
