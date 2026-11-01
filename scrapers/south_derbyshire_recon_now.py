"""Recon: what does the South Derbyshire planning page look like now? (no writes)"""
import asyncio, re
from playwright.async_api import async_playwright

URL = "https://planning.southderbyshire.gov.uk/"
CTX = {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
       "viewport": {"width": 1280, "height": 900}, "locale": "en-GB", "ignore_https_errors": True}

async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await (await b.new_context(**CTX)).new_page()
        resp = await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        print("status", resp.status if resp else None, "| final url", page.url)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            print("(network never went idle)")
        await page.wait_for_timeout(3000)
        print("title:", await page.title())
        body = await page.inner_text("body")
        print("body text (first 1500 chars):\n", re.sub(r"\n{3,}", "\n\n", body)[:1500])
        print("\n--- selects / inputs / buttons / links ---")
        for sel in ("select", "input", "button"):
            for el in await page.query_selector_all(sel):
                attrs = await el.evaluate("e => Object.fromEntries([...e.attributes].map(a => [a.name, a.value.slice(0,80)]))")
                print(sel, attrs)
        print("\nlinks to search/applications:")
        for a in (await page.query_selector_all("a"))[:60]:
            h, t = await a.get_attribute("href"), (await a.inner_text()).strip()[:60]
            if h and re.search(r"search|application|planning|map", h + t, re.I):
                print("  ", t, "->", h)
        print("\nwire:* attribute names present:", sorted(set(re.findall(r"wire:[a-z.\-:]+", await page.content())))[:40])
        await b.close()

asyncio.run(main())
