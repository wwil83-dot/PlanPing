#!/usr/bin/env python3
"""
Bath & North East Somerset weekly list diagnostic (2026-10-04, rev 3).

CONFIRMED from rev 2's real output:
  - the status dropdown is select#weeklyListOption (Validated, Decided)
  - the week dropdown is select#weeklyListBetween; its FIRST option is
    the coming week (05/10/2026 to 11/10/2026), which is why the
    "latest week" had no applications — it hasn't happened yet
  - rev 2's submit heuristic clicked the first visible "Search" button,
    which is the council's site-wide header search; every combination
    landed on bathnes.gov.uk/search-page — so rev 2's zero counts say
    nothing about whether those weeks have data
  - the page fetches JSON from api.bathnes.gov.uk (PlanningAPI v2),
    whose robots.txt disallows automated access, so the scraper should
    drive the public page, not call the API directly

This revision:
  1. clicks the Search button INSIDE the weekly list form (nearest
     ancestor of the week dropdown that contains a Search control)
  2. records refs both before and after the click, to show whether
     picking a week auto-loads results
  3. logs what the page's OWN api.bathnes.gov.uk planning calls return
     (method, post data, body snippet) — observation only, nothing is
     requested beyond what the page itself does
"""
import asyncio
import re

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
REF_RE = re.compile(r"\b\d{2}/\d{4,5}/[A-Z]{2,6}\b")
SECTION_XPATH = ("xpath=ancestor::*[.//button[normalize-space()='Search'] "
                 "or .//input[@value='Search']][1]")

api_calls: dict[str, dict] = {}


async def settle(page, extra=1.5):
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def dismiss_banners(page):
    for sel in ["button:has-text('Accept')", "button:has-text('I agree')",
                "button:has-text('Allow')"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            try:
                await loc.first.click(timeout=3_000)
                print(f"  cookie-style banner dismissed via {sel!r}")
            except Exception:
                pass
            break


async def refs_on_page(page):
    body = await page.locator("body").inner_text()
    return sorted(set(REF_RE.findall(body))), body


async def run_combo(page, week_pos, status_word):
    await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
    await settle(page)
    await dismiss_banners(page)

    result = {"week_pos": week_pos, "status": status_word}
    status = page.locator("select#weeklyListOption")
    week = page.locator("select#weeklyListBetween")
    if await status.count() == 0 or await week.count() == 0:
        return {**result, "error": "weekly list selects not found"}

    # Status first (picking a week may navigate immediately), then week.
    await status.select_option(label=status_word, timeout=5_000)
    await settle(page, 1.5)

    options = [o.strip() for o in await week.locator("option").all_text_contents()]
    if week_pos >= len(options):
        return {**result, "error": f"only {len(options)} week options"}
    await week.select_option(index=week_pos, timeout=5_000)
    result["week_label"] = options[week_pos]
    await settle(page, 2)

    pre_refs, _ = await refs_on_page(page)
    result["refs_before_click"] = len(pre_refs)

    section = week.locator(SECTION_XPATH)
    if await section.count() == 0:
        return {**result, "error": "no ancestor of the week dropdown contains a Search control"}
    btn = section.locator("button:has-text('Search'), input[value='Search']").first
    info = await btn.evaluate(
        "el => ({tag: el.tagName, id: el.id, cls: el.className, visible: el.offsetParent !== null})")
    result["search_button"] = info
    try:
        await btn.click(timeout=4_000)
        result["click"] = "normal"
    except Exception:
        await btn.evaluate("el => el.click()")
        result["click"] = "js-fallback"
    await settle(page, 3)

    refs, _ = await refs_on_page(page)
    result.update({"url": page.url, "refs_after_click": len(refs),
                   "sample_refs": refs[:4],
                   "table_rows": await page.locator("table tbody tr").count()})
    return result


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        async def on_response(resp):
            try:
                url = resp.url
                if "api.bathnes.gov.uk" not in url or "json" not in resp.headers.get("content-type", "").lower():
                    return
                if "OSHubToken" in url:
                    return  # map token — irrelevant and shouldn't be logged
                if url in api_calls:
                    return
                body = (await resp.text())[:1800]
                api_calls[url] = {
                    "status": resp.status, "method": resp.request.method,
                    "post_data": (resp.request.post_data or "")[:400], "body": body}
            except Exception:
                pass

        page.on("response", on_response)

        print("=" * 60)
        print("STEP 1: combinations (status set first, then week)")
        print("=" * 60)
        summaries = []
        for status_word in ("Validated", "Decided"):
            for pos in range(4):
                try:
                    s = await run_combo(page, pos, status_word)
                except Exception as e:
                    s = {"week_pos": pos, "status": status_word,
                         "error": f"{type(e).__name__}: {e}"}
                summaries.append(s)
                print(f"  {s}")

        print("\n" + "=" * 60)
        print("STEP 2: real results markup for the first combination with data")
        print("=" * 60)
        winner = next((s for s in summaries if s.get("refs_after_click", 0) > 0), None)
        if winner is None:
            print("  no combination produced application references")
            refs, body = await refs_on_page(page)
            print(f"  last page body text (first 1500 chars):\n{body[:1500]!r}")
        else:
            await run_combo(page, winner["week_pos"], winner["status"])
            refs, body = await refs_on_page(page)
            print(f"  used: week {winner['week_label']!r}, status {winner['status']!r}")
            print(f"  body text (first 2500 chars):\n{body[:2500]!r}")

            tables = page.locator("table")
            print(f"\n  <table> elements: {await tables.count()}")
            for i in range(await tables.count()):
                t = tables.nth(i)
                if REF_RE.search(await t.inner_text()):
                    print(f"  table {i} holds references; first 3500 chars of HTML:")
                    print((await t.evaluate("el => el.outerHTML"))[:3500])
                    break
            else:
                print("  no <table> holds references — probing for a list/card layout")
                holder = page.locator(f"text={refs[0]}").first
                chain = await holder.evaluate(
                    "el => { const out=[]; let n=el; for (let i=0;i<6&&n;i++){ "
                    "out.push(n.tagName+'.'+(n.className||'')+'#'+(n.id||'')); n=n.parentElement;} "
                    "return out; }")
                print(f"  ancestor chain of the first reference: {chain}")
                block = await holder.evaluate(
                    "el => { let n=el; for (let i=0;i<4&&n.parentElement;i++) n=n.parentElement; "
                    "return n.outerHTML; }")
                print(f"  surrounding block HTML (first 3000 chars):\n{block[:3000]}")

            pag = page.locator("[class*='pag' i], [id*='pag' i], a:has-text('Next'), "
                               "button:has-text('Next'), button:has-text('Load more')")
            print(f"\n  pagination-like elements: {await pag.count()}")
            for i in range(min(await pag.count(), 6)):
                info = await pag.nth(i).evaluate(
                    "el => ({tag: el.tagName, id: el.id, cls: el.className, "
                    "text: el.textContent.trim().slice(0,50), visible: el.offsetParent !== null})")
                print(f"    [{i}] {info}")

        print("\n" + "=" * 60)
        print("STEP 3: the page's own api.bathnes.gov.uk JSON calls (observed only)")
        print("=" * 60)
        if not api_calls:
            print("  none seen")
        for url, c in api_calls.items():
            print(f"\n  {c['method']} {c['status']} {url[:220]}")
            if c["post_data"]:
                print(f"    request body: {c['post_data']}")
            print(f"    response (first 600 chars): {c['body'][:600]!r}")

        await context.close()
        await browser.close()
    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
