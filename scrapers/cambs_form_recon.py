#!/usr/bin/env python3
"""
PlanFind recon (2026-11-13): Cambridgeshire County Council's new planning register
(planning.cambridgeshire.gov.uk, same platform family as Welwyn Hatfield / Worcester) has no #DateReceivedFrom
field on /Search/Advanced. This lists every input/select/checkbox on that page (id, name, type, label, visible)
and tries a Quick search, so we can pin the real field names. Read-only; saves nothing.
"""
import asyncio, re
from playwright.async_api import async_playwright

BASE = "https://planning.cambridgeshire.gov.uk"
JS = """() => [...document.querySelectorAll('input,select,textarea,button')].map(e => {
  const lab = (e.id && document.querySelector('label[for="'+e.id+'"]')) || e.closest('label');
  const r = e.getBoundingClientRect();
  return {tag:e.tagName.toLowerCase(), id:e.id, name:e.name, type:e.type, ph:e.placeholder||'', val:(e.value||'').slice(0,30),
          label:(lab?lab.innerText:'').trim().slice(0,60), vis: !!(r.width||r.height), form:(e.form&&e.form.getAttribute('action'))||''};
})"""

async def dump(page, title):
    print(f"\n=== {title}: {page.url}  (title: {await page.title()!r}) ===")
    for f in await page.evaluate(JS):
        print("  ", f)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await b.new_context(ignore_https_errors=True, locale="en-GB", viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        for path in ("/Search/Advanced", "/Search/Quick", "/"):
            try:
                await page.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
                try: await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception: pass
                await dump(page, path)
            except Exception as e:
                print(f"\n=== {path}: FAILED {type(e).__name__}: {e}")
        # headings / section text on Advanced, to see where the date fields live
        try:
            await page.goto(BASE + "/Search/Advanced", wait_until="domcontentloaded", timeout=45000)
            txt = await page.inner_text("body")
            print("\n=== Advanced page text (first 2500 chars) ===\n", re.sub(r"\n{2,}", "\n", txt)[:2500])
        except Exception as e:
            print("text dump failed:", e)
        await b.close()

asyncio.run(main())
