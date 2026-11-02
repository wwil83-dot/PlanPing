"""Recon 2 (no writes): drive the redesigned South Derbyshire search and dump what comes back."""
import asyncio, re
from datetime import date, timedelta
from playwright.async_api import async_playwright

URL = "https://planning.southderbyshire.gov.uk/"
CTX = {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
       "viewport": {"width": 1280, "height": 900}, "locale": "en-GB", "ignore_https_errors": True}

async def settle(page, ms=4000):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(ms)

async def options(page, name):
    loc = page.locator(f"select[wire\\:model\\.live='{name}']").first
    return await loc.evaluate("s => [...s.options].map(o => [o.value, o.text.trim()])")

async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await (await b.new_context(**CTX)).new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await settle(page)
        for n in ("datePreset", "perPage", "sort", "status"):
            print(f"options {n}:", await options(page, n))

        print("\n--- 'Filter dates by' control HTML ---")
        html = await page.evaluate("""() => {
            const els = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && /Filter dates by/.test(e.textContent));
            return els.slice(0, 2).map(e => (e.parentElement.parentElement || e.parentElement).outerHTML.slice(0, 2500));
        }""")
        for h in html:
            print(re.sub(r"\s+", " ", h), "\n")

        print("--- choose dates: last 30 days ---")
        await page.locator("select[wire\\:model\\.live='datePreset']").first.select_option(label="Choose dates")
        await settle(page, 2000)
        a = (date.today() - timedelta(days=30)).isoformat()
        z = date.today().isoformat()
        for name, val in (("afterDate", a), ("beforeDate", z)):
            loc = page.locator(f"input[wire\\:model\\.live='{name}']")
            n = await loc.count()
            vis = [i for i in range(n) if await loc.nth(i).is_visible()]
            print(name, "inputs:", n, "visible:", vis)
            if vis:
                await loc.nth(vis[0]).fill(val)
                await loc.nth(vis[0]).blur()
            await settle(page, 2500)

        links = await page.eval_on_selector_all("a[href*='/application/']", "els => [...new Set(els.map(e => e.href))].length")
        print("distinct application links after date filter:", links)
        body = await page.inner_text("body")
        for m in re.finditer(r"[^\n]*(?:results?|Showing|of \d[\d,]*)[^\n]*", body):
            print("count text:", m.group(0).strip()[:150])
            break

        print("\n--- first result row HTML ---")
        row = await page.evaluate("""() => {
            const a = document.querySelector("a[href*='/application/']");
            if (!a) return null;
            const r = a.closest('tr') || a.closest('li') || a.closest('article') || a.parentElement.parentElement;
            return {tag: r.tagName, html: r.outerHTML.slice(0, 3500), text: r.innerText.slice(0, 600)};
        }""")
        print(re.sub(r"\s+", " ", str(row)))

        print("\n--- wire:effects attributes ---")
        effs = await page.evaluate("""() => [...document.querySelectorAll('[wire\\\\:effects]')].map(e => (e.getAttribute('wire:effects') || '').slice(0, 1500))""")
        print(len(effs), "elements")
        for e in effs[:3]:
            print(len(e), e[:1500], "\n")

        # try the biggest page size
        opts = await options(page, "perPage")
        big = opts[-1][0]
        await page.locator("select[wire\\:model\\.live='perPage']").first.select_option(big)
        await settle(page, 3000)
        links2 = await page.eval_on_selector_all("a[href*='/application/']", "els => [...new Set(els.map(e => e.href))].length")
        print(f"after perPage={big}: distinct application links:", links2)
        await b.close()

asyncio.run(main())
