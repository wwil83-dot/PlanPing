#!/usr/bin/env python3
"""
Bath & North East Somerset per-application link diagnostic (2026-10-04).

Goal: find a URL that opens ONE application on the council's own site,
so each PlanFind application can offer "view on council portal" like
the other councils. The scraper's data has no link field and the page
is a single-page app, so this checks, on a real week of results:
  1. the real markup of one result and every link/button inside it
  2. what clicking a result does (URL change? new tab? just a panel?)
  3. whether the URL reached that way works when opened FRESH — i.e.
     whether a real deep link exists
  4. a fallback: the basic "Application Reference or Address" search —
     what URL and results it produces for a known reference
"""
import asyncio
import re
from datetime import date

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

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

URL = "https://app.bathnes.gov.uk/webforms/planning/search.html#weeklyList"
BASE = "https://app.bathnes.gov.uk/webforms/planning/search.html"
REF_RE = re.compile(r"\b\d{2}/\d{4,5}/[A-Z]{2,6}\b")
WEEK_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+to")
SECTION_XPATH = ("xpath=ancestor::*[.//button[normalize-space()='Search'] "
                 "or .//input[@value='Search']][1]")

CONTROLS_JS = """
el => Array.from(el.querySelectorAll('a, button')).slice(0, 25).map(c => ({
  tag: c.tagName, text: (c.textContent || '').trim().slice(0, 50),
  href: c.getAttribute('href'), onclick: c.getAttribute('onclick'),
  target: c.getAttribute('target'), id: c.id,
  cls: (c.className || '').toString().slice(0, 40)
}))
"""

BLOCK_JS = """
el => {
  let n = el;
  for (let i = 0; i < 8 && n; i++) {
    if (n !== el && n.querySelector && n.querySelector('a[href], button')) return n.outerHTML;
    n = n.parentElement;
  }
  return el.parentElement ? el.parentElement.outerHTML : el.outerHTML;
}
"""

CHAIN_JS = """
el => { const out = []; let n = el;
  for (let i = 0; i < 8 && n; i++) {
    out.push(n.tagName + '.' + (n.className || '').toString().slice(0, 40) + '#' + (n.id || ''));
    n = n.parentElement; }
  return out; }
"""


def first_usable_week_index(labels, today):
    for i, label in enumerate(labels):
        m = WEEK_RE.search(label)
        if m and date(int(m.group(3)), int(m.group(2)), int(m.group(1))) <= today:
            return i
    return None


async def settle(page, extra=1.5):
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)

        opened_tabs = []
        api_after = []
        capture = {"on": False}

        page = await context.new_page()
        context.on("page", lambda p: opened_tabs.append(p))

        async def on_response(resp):
            try:
                if (capture["on"] and "api.bathnes.gov.uk" in resp.url
                        and "OSHubToken" not in resp.url
                        and "json" in resp.headers.get("content-type", "").lower()):
                    api_after.append((resp.request.method, resp.status, resp.url[:200],
                                      (resp.request.post_data or "")[:200],
                                      (await resp.text())[:250]))
            except Exception:
                pass

        page.on("response", on_response)

        # ---- a real week of results
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        await settle(page)
        await page.locator("select#weeklyListOption").select_option(label="Validated", timeout=8_000)
        await settle(page, 1.5)
        week = page.locator("select#weeklyListBetween")
        labels = [o.strip() for o in await week.locator("option").all_text_contents()]
        idx = first_usable_week_index(labels, date.today())
        await week.select_option(index=idx, timeout=8_000)
        print(f"Week used: {labels[idx]!r}")
        await settle(page, 1.0)
        await page.locator("button#weeklySearchBtn").click(timeout=8_000)
        await settle(page, 3)

        body = await page.locator("body").inner_text()
        refs = sorted(set(REF_RE.findall(body)))
        print(f"Applications visible on the results screen: {len(refs)}")
        print(f"URL on the results screen: {page.url}")
        if not refs:
            print("No references visible — cannot continue.")
            await browser.close()
            return
        ref = refs[0]
        print(f"Reference examined: {ref}")

        # ---- 1. markup of one result
        print("\n" + "=" * 60)
        print("STEP 1: markup of one result and the links/buttons inside it")
        print("=" * 60)
        holder = page.locator(f"text={ref}").first
        print(f"ancestor chain: {await holder.evaluate(CHAIN_JS)}")
        block = await holder.evaluate(BLOCK_JS)
        print(f"nearest ancestor holding a link/button (first 3500 chars):\n{block[:3500]}")
        controls = await holder.evaluate(
            "el => { let n = el; for (let i=0;i<8&&n;i++){ "
            "if (n!==el && n.querySelector && n.querySelector('a[href], button')) return n; "
            "n = n.parentElement; } return el.parentElement; }")
        # (controls handle only used to scope the listing below)
        scope = await holder.evaluate_handle(
            "el => { let n = el; for (let i=0;i<8&&n;i++){ "
            "if (n!==el && n.querySelector && n.querySelector('a[href], button')) return n; "
            "n = n.parentElement; } return el.parentElement; }")
        links = await scope.evaluate(CONTROLS_JS)
        print("\nlinks/buttons in that block:")
        for l in links:
            print(f"   {l}")

        # ---- 2. click the result
        print("\n" + "=" * 60)
        print("STEP 2: clicking the result")
        print("=" * 60)
        url_before = page.url
        tabs_before = len(opened_tabs)
        capture["on"] = True
        candidate = page.locator(f"a:has-text('{ref}')")
        target_desc = "anchor containing the reference"
        if await candidate.count() == 0:
            candidate = page.locator(f"text={ref}")
            target_desc = "the reference text itself"
        print(f"clicking: {target_desc}")
        try:
            await candidate.first.click(timeout=6_000)
        except Exception as e:
            print(f"  click failed: {type(e).__name__}: {str(e)[:160]}")
        await settle(page, 3)
        capture["on"] = False

        view = opened_tabs[-1] if len(opened_tabs) > tabs_before else page
        if view is not page:
            await settle(view, 2)
        print(f"new tab opened: {view is not page}")
        print(f"URL before click: {url_before}")
        print(f"URL after click:  {view.url}")
        detail_body = await view.locator("body").inner_text()
        print(f"detail view body text (first 1500 chars):\n{detail_body[:1500]!r}")
        print(f"reference shown in detail view: {ref in detail_body}")
        anchors = await view.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]')).filter(a => a.offsetParent !== null)"
            ".slice(0, 20).map(a => ({text: a.textContent.trim().slice(0,40), href: a.getAttribute('href')}))")
        print("visible links in the detail view:")
        for a in anchors:
            print(f"   {a}")
        keyword_controls = await view.evaluate(
            "() => Array.from(document.querySelectorAll('a, button'))"
            ".filter(e => /link|share|print|portal|full details|permalink/i.test(e.textContent || ''))"
            ".slice(0, 10).map(e => ({tag: e.tagName, text: e.textContent.trim().slice(0,50), "
            "href: e.getAttribute('href'), onclick: e.getAttribute('onclick')}))")
        print(f"controls mentioning link/share/print/portal: {keyword_controls}")
        print("\nAPI calls the click triggered:")
        for m, st, u, pd, b in api_after:
            print(f"   {m} {st} {u}\n      request: {pd!r}\n      response start: {b!r}")
        if not api_after:
            print("   none")

        # ---- 3. deep link test
        print("\n" + "=" * 60)
        print("STEP 3: does the URL reached by clicking work when opened fresh?")
        print("=" * 60)
        reached = view.url
        if reached == url_before:
            print("The address did not change — the detail view has no URL of its own.")
        else:
            fresh = await context.new_page()
            await fresh.goto(reached, wait_until="domcontentloaded", timeout=45_000)
            await settle(fresh, 3)
            fresh_body = await fresh.locator("body").inner_text()
            print(f"opened fresh: {reached}")
            print(f"reference present on the freshly loaded page: {ref in fresh_body}")
            print(f"fresh page body (first 800 chars):\n{fresh_body[:800]!r}")
            await fresh.close()

        # ---- 4. basic search fallback
        print("\n" + "=" * 60)
        print("STEP 4: basic search by reference — what URL/results does it give?")
        print("=" * 60)
        try:
            bp = await context.new_page()
            await bp.goto(BASE, wait_until="domcontentloaded", timeout=45_000)
            await settle(bp)
            box = bp.get_by_label("Application Reference or Address")
            await box.fill(ref, timeout=6_000)
            section = box.locator(SECTION_XPATH)
            btn = section.locator("button:has-text('Search'), input[value='Search']").first
            await btn.click(timeout=6_000)
            await settle(bp, 3)
            print(f"URL after basic search: {bp.url}")
            bbody = await bp.locator("body").inner_text()
            print(f"reference in results: {ref in bbody}")
            print(f"results body (first 1200 chars):\n{bbody[:1200]!r}")
            blinks = await bp.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).filter(a => a.offsetParent !== null)"
                ".slice(0, 15).map(a => ({text: a.textContent.trim().slice(0,40), href: a.getAttribute('href')}))")
            for l in blinks:
                print(f"   {l}")
            await bp.close()
        except Exception as e:
            print(f"  basic search step failed: {type(e).__name__}: {str(e)[:200]}")

        await context.close()
        await browser.close()
    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
