#!/usr/bin/env python3
"""
Medway ODP register pagination diagnostic (2026-10-04).

Reported problem: the Medway scraper only ever gets 10 applications and
returns the same ones every night, although the register has many more.
Medway last saved on 13 September.

Reading medway_scraper.py, three different causes would all look like
"the same 10 every time":
  A. the ?page=N URL parameter is ignored (page 1 is served every time).
     The loop has NO same-page detection, so it would quietly run to
     MAX_PAGES and the seen_refs dedupe would hide that.
  B. pages DO differ, but the "Next page" link isn't recognised
     (rel="next" is matched as an exact one-element list), so the loop
     stops after page 1.
  C. pages work and the scraper does fetch many, but the register sorts
     by PUBLISHED date, and every card's RECEIVED date falls outside the
     30-day DAYS_BACK window, so everything is filtered out and nothing
     is saved (last_saved_at only moves when something is saved).
This checks each directly, on the live register.
"""
import asyncio
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from medway_councils import BASE_URL

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1280, "height": 900},
    "locale": "en-GB",
    "ignore_https_errors": True,
}
DAYS_BACK = 30
COUNT_RE = re.compile(r"(?:showing|displaying)?[^\d\n]{0,20}(\d[\d,]*)\s+(?:results?|applications?|planning applications?)", re.I)


def parse_cards(html: str) -> list[dict]:
    """Same dt/dd approach as the production scraper."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.find_all("article", class_="dpr-application-card"):
        fields = {}
        for dl in card.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                fields[dt.get_text(strip=True)] = dd.get_text(strip=True)
        if fields.get("Application reference"):
            out.append({"ref": fields["Application reference"],
                        "received": fields.get("Received date", ""),
                        "published": fields.get("Published date", ""),
                        "status": fields.get("Status", "")})
    return out


def parse_date(s: str):
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def within_window(cards: list[dict], today: date, days: int) -> int:
    cutoff = today - timedelta(days=days)
    return sum(1 for c in cards if (d := parse_date(c["received"])) and d >= cutoff)


def find_count_text(body: str):
    m = COUNT_RE.search(body or "")
    return m.group(0).strip() if m else None


async def settle(page, extra=1.0):
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def load(page, url):
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    await settle(page)
    return resp.status if resp else None


def show(label, cards):
    refs = [c["ref"] for c in cards]
    print(f"  {label}: {len(cards)} cards  first refs: {refs[:3]}")
    return refs


async def main():
    today = date.today()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}")
        print(f"BASE_URL from medway_councils.py: {BASE_URL}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        # ---------------------------------------------------------- STEP 1
        print("=" * 60)
        print("STEP 1: page 1, exactly as the production scraper requests it")
        print("=" * 60)
        url1 = f"{BASE_URL}?page=1&resultsPerPage=10&type=simple"
        status = await load(page, url1)
        try:
            btn = page.get_by_text("Accept analytics cookies", exact=True)
            if await btn.count() > 0:
                await btn.first.click(timeout=4_000)
                await asyncio.sleep(1)
        except Exception:
            pass
        html1 = await page.content()
        cards1 = parse_cards(html1)
        print(f"  HTTP {status}   final URL: {page.url}")
        refs1 = show("page 1", cards1)
        body = await page.locator("body").inner_text()
        print(f"  result-count text found: {find_count_text(body)!r}")
        print("  received / published / status of the cards on page 1:")
        for c in cards1:
            print(f"     {c['ref']:16} received={c['received']:12} published={c['published']:12} {c['status']}")
        print(f"  cards received within the last {DAYS_BACK} days: "
              f"{within_window(cards1, today, DAYS_BACK)} of {len(cards1)}  "
              f"(the production scraper discards the rest)")

        # ---------------------------------------------------------- STEP 2
        print("\n" + "=" * 60)
        print("STEP 2: the pagination controls as they really are")
        print("=" * 60)
        navs = page.locator("nav[aria-label*='agination' i], .govuk-pagination, [class*='pagination' i]")
        print(f"  pagination-like containers: {await navs.count()}")
        if await navs.count() > 0:
            print("  first container HTML (up to 2500 chars):")
            print((await navs.first.evaluate("el => el.outerHTML"))[:2500])
        rel_links = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[rel]')).map(a => "
            "({rel: a.getAttribute('rel'), href: a.getAttribute('href'), text: a.textContent.trim().slice(0,30)}))")
        print(f"  <a rel=...> elements: {rel_links}")
        page_links = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a')).filter(a => "
            "/next|prev|^\\s*\\d+\\s*$/i.test(a.textContent.trim())).slice(0,15).map(a => "
            "({text: a.textContent.trim().slice(0,30), href: a.getAttribute('href'), rel: a.getAttribute('rel')}))")
        print("  links whose text looks like Next / Previous / a page number:")
        for l in page_links:
            print(f"     {l}")
        rpp = await page.evaluate(
            "() => Array.from(document.querySelectorAll('select, a')).filter(e => "
            "/resultsPerPage|per.?page|results.?per/i.test((e.name||'') + (e.id||'') + (e.getAttribute('href')||'') + e.textContent))"
            ".slice(0,8).map(e => ({tag: e.tagName, name: e.name, id: e.id, "
            "href: e.getAttribute('href'), text: e.textContent.trim().slice(0,60)}))")
        print(f"  results-per-page controls: {rpp}")

        # ---------------------------------------------------------- STEP 3
        print("\n" + "=" * 60)
        print("STEP 3: does the ?page=N URL parameter actually change the results?  (cause A)")
        print("=" * 60)
        for n in (2, 3):
            status = await load(page, f"{BASE_URL}?page={n}&resultsPerPage=10&type=simple")
            cards = parse_cards(await page.content())
            refs = show(f"?page={n} (HTTP {status}, URL now {page.url})", cards)
            print(f"    identical to page 1: {refs == refs1 and bool(refs)}")

        # ---------------------------------------------------------- STEP 4
        print("\n" + "=" * 60)
        print("STEP 4: clicking the real Next link  (cause B)")
        print("=" * 60)
        await load(page, url1)
        nxt = page.locator("a[rel~='next'], a:has-text('Next')")
        print(f"  Next-link candidates: {await nxt.count()}")
        if await nxt.count() > 0:
            info = await nxt.first.evaluate(
                "el => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))")
            print(f"  attributes: {info}")
            try:
                await nxt.first.click(timeout=8_000)
                await settle(page, 1.5)
                cards = parse_cards(await page.content())
                refs = show(f"after clicking Next (URL now {page.url})", cards)
                print(f"    identical to page 1: {refs == refs1 and bool(refs)}")
            except Exception as e:
                print(f"  click failed: {type(e).__name__}: {str(e)[:160]}")
        else:
            print("  no Next link found by rel or by text")

        # ---------------------------------------------------------- STEP 5
        print("\n" + "=" * 60)
        print("STEP 5: would a bigger page size help?")
        print("=" * 60)
        for size in (50, 100):
            status = await load(page, f"{BASE_URL}?page=1&resultsPerPage={size}&type=simple")
            cards = parse_cards(await page.content())
            print(f"  resultsPerPage={size}: HTTP {status}, {len(cards)} cards, "
                  f"within {DAYS_BACK} days: {within_window(cards, today, DAYS_BACK)}")

        await context.close()
        await browser.close()
    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
