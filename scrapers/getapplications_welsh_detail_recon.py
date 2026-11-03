"""Recon (no writes): what do the Gwynedd / Ceredigion application detail pages look like, and does the family scraper's
recheck parser find a decision on them? Opens the 3 oldest pending applications of each council."""
import asyncio, os, re
import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from getapplications_scraper import _parse_detail_page, _extract_id_from_url, CONTEXT_OPTIONS, BROWSER_ARGS, SUPABASE_URL, SUPABASE_KEY

BASES = {574: "https://amg.gwynedd.llyw.cymru", 575: "https://ceredigion-online.tascomi.com"}


async def pending(cid, oldest=True, n=3):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/planning_applications",
                        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                        params={"select": "reference,council_url,submitted_date", "council_id": f"eq.{cid}", "status": "eq.pending",
                                "order": f"submitted_date.{'asc' if oldest else 'desc'}", "limit": str(n)})
        return r.json()


async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        ctx = await b.new_context(**CONTEXT_OPTIONS)
        page = await ctx.new_page()
        for cid, base in BASES.items():
            rows = await pending(cid, True, 3) + await pending(cid, False, 1)
            print(f"\n######## council {cid}: {len(rows)} applications to open")
            for row in rows:
                app_id = _extract_id_from_url(row.get("council_url") or "")
                url = f"{base}/planning/index.html?fa=getApplication&id={app_id}"
                print(f"\n=== {row['reference']} (submitted {row['submitted_date']}) id={app_id}\n    {row.get('council_url')}")
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                except Exception as e:
                    print("  load failed:", type(e).__name__)
                    continue
                html = await page.content()
                print(f"  HTTP {resp.status if resp else None}; title {await page.title()!r}; {len(html)} chars; "
                      f"human-verification page: {'Human Verification' in html}")
                fields = _parse_detail_page(html)
                print("  parser fields:", {k: v[:50] for k, v in fields.items()})
                print("  Decision found by the scraper:", repr(fields.get('Decision')), "| issued:", repr(fields.get('Decision Issued Date')))
                text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
                text = re.sub(r"\n{2,}", "\n", text)
                print("  --- page text (first 1800 chars) ---")
                print(text[:1800])
                await asyncio.sleep(6)
        await b.close()

asyncio.run(main())
