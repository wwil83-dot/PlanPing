#!/usr/bin/env python3
"""
Bath & North East Somerset weekly list diagnostic (2026-10-02, rev 2).

Real, direct description from the person running this project: the
page at app.bathnes.gov.uk/webforms/planning/search.html#weeklyList
has a weekly dropdown; picking a week moves to another screen showing
applications and a map. A SECOND dropdown, Validated vs Decided, also
exists (mentioned after the first version of this script was written).
The latest week returned nothing, the week before had plenty.

Because picking a week appears to navigate immediately, the status
dropdown is set FIRST, then the week dropdown is re-located (its
options could change with the status) and the week picked.

Checks:
  1. every select/button on the landing view with real ids and options
  2. for the first four weeks x both statuses, how many application
     references actually appear (a layout-agnostic count, so it works
     before the real results markup is known)
  3. the real results markup for the first combination that has data
  4. any JSON responses fetched while loading results
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
SUBMIT_TEXT_RE = re.compile(r"search|go|view|show|submit|find|list", re.I)
PLACEHOLDER_RE = re.compile(r"select|choose|please", re.I)
STATUS_RE = re.compile(r"validated|decided", re.I)


def real_options(opts):
    return [(i, o.strip()) for i, o in enumerate(opts)
            if o and o.strip() and not PLACEHOLDER_RE.search(o)]


def looks_like_status(opts):
    return any(STATUS_RE.search(o) for _, o in real_options(opts))


def looks_like_week(opts):
    real = real_options(opts)
    if len(real) < 2 or looks_like_status(opts):
        return False
    with_digits = sum(bool(re.search(r"\d", o)) for _, o in real)
    return with_digits >= max(2, len(real) // 2)


async def settle(page, extra=1.5):
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def dismiss_banners(page):
    for sel in ["button:has-text('Accept')", "button:has-text('I agree')",
                "button:has-text('OK')", "button:has-text('Allow')"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            try:
                await loc.first.click(timeout=3_000)
                print(f"  cookie-style banner dismissed via {sel!r}")
            except Exception:
                pass
            break


async def find_selects(page):
    """Returns (week_select, week_opts, status_select, status_opts)."""
    week = status = None
    week_opts = status_opts = []
    selects = page.locator("select")
    for i in range(await selects.count()):
        el = selects.nth(i)
        if not await el.is_visible():
            continue
        opts = [o.strip() for o in await el.locator("option").all_text_contents()]
        if status is None and looks_like_status(opts):
            status, status_opts = el, opts
        elif week is None and looks_like_week(opts):
            week, week_opts = el, opts
    return week, week_opts, status, status_opts


async def run_combo(page, week_pos, status_word):
    """Fresh load; set status first, then pick the Nth real week."""
    await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
    await settle(page)
    await dismiss_banners(page)

    week, week_opts, status, status_opts = await find_selects(page)
    result = {"week_pos": week_pos, "status": status_word}

    if status is not None:
        match = next(((i, o) for i, o in real_options(status_opts)
                      if status_word.lower() in o.lower()), None)
        if match is None:
            return {**result, "error": f"no status option matching {status_word!r}: {status_opts}"}
        await status.select_option(index=match[0], timeout=5_000)
        await settle(page, 2)
        # The week dropdown may have been re-rendered by the status change.
        week, week_opts, _, _ = await find_selects(page)
    else:
        result["note"] = "no status dropdown located — week only"

    if week is None:
        return {**result, "error": "no week dropdown located"}
    real = real_options(week_opts)
    if week_pos >= len(real):
        return {**result, "error": f"only {len(real)} real week options"}
    idx, label = real[week_pos]
    await week.select_option(index=idx, timeout=5_000)
    await settle(page, 2)

    clicked = None
    btns = page.locator("button:visible, input[type='submit']:visible, input[type='button']:visible")
    for i in range(await btns.count()):
        b = btns.nth(i)
        text = ((await b.text_content()) or (await b.get_attribute("value")) or "").strip()
        if text and SUBMIT_TEXT_RE.search(text) and "cookie" not in text.lower():
            try:
                await b.click(timeout=4_000)
                clicked = text
                await settle(page, 2)
                break
            except Exception:
                continue

    body = await page.locator("body").inner_text()
    refs = sorted(set(REF_RE.findall(body)))
    return {**result, "week_label": label, "clicked_button": clicked,
            "url": page.url, "unique_refs": len(refs), "sample_refs": refs[:4],
            "table_rows": await page.locator("table tbody tr").count()}


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        json_hits = []

        async def on_response(resp):
            try:
                ctype = resp.headers.get("content-type", "")
                if "json" in ctype.lower():
                    json_hits.append((resp.status, resp.url[:200], ctype))
            except Exception:
                pass

        page.on("response", on_response)

        print("=" * 60)
        print("STEP 1: controls on the landing view")
        print("=" * 60)
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        await settle(page)
        print(f"  title: {await page.title()!r}")
        print(f"  url:   {page.url}")
        await dismiss_banners(page)

        selects = page.locator("select")
        print(f"\n  <select> elements: {await selects.count()}")
        for i in range(await selects.count()):
            el = selects.nth(i)
            info = await el.evaluate(
                "el => ({id: el.id, name: el.name, cls: el.className, "
                "onchange: el.getAttribute('onchange'), visible: el.offsetParent !== null})")
            opts = [o.strip() for o in await el.locator("option").all_text_contents()]
            kind = ("STATUS" if looks_like_status(opts)
                    else "WEEK" if looks_like_week(opts) else "other")
            print(f"    [{i}] ({kind}) {info}")
            print(f"        options ({len(opts)}): {opts[:10]}")

        btns = page.locator("button, input[type='submit'], input[type='button']")
        print(f"\n  button-like controls: {await btns.count()}")
        for i in range(min(await btns.count(), 12)):
            info = await btns.nth(i).evaluate(
                "el => ({tag: el.tagName, id: el.id, cls: el.className, "
                "text: (el.textContent || el.value || '').trim().slice(0,40), "
                "visible: el.offsetParent !== null})")
            print(f"    [{i}] {info}")

        print("\n" + "=" * 60)
        print("STEP 2: first four weeks x Validated / Decided")
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
        print("STEP 3: real results markup for the first combination with data")
        print("=" * 60)
        winner = next((s for s in summaries if s.get("unique_refs", 0) > 0), None)
        if winner is None:
            print("  no combination produced application references")
        else:
            await run_combo(page, winner["week_pos"], winner["status"])
            body = await page.locator("body").inner_text()
            print(f"  used: week {winner['week_label']!r}, status {winner['status']!r}")
            print(f"  body text (first 2500 chars):\n{body[:2500]!r}")

            tables = page.locator("table")
            print(f"\n  <table> elements: {await tables.count()}")
            for i in range(await tables.count()):
                t = tables.nth(i)
                if REF_RE.search(await t.inner_text()):
                    print(f"  table {i} holds references; first 3500 chars of its HTML:")
                    print((await t.evaluate("el => el.outerHTML"))[:3500])
                    break
            else:
                print("  no <table> holds references — probing for a list/card layout")
                first_ref = sorted(set(REF_RE.findall(body)))[0]
                holder = page.locator(f"text={first_ref}").first
                chain = await holder.evaluate(
                    "el => { const out=[]; let n=el; for (let i=0;i<6&&n;i++){ "
                    "out.push(n.tagName+'.'+(n.className||'')+'#'+(n.id||'')); n=n.parentElement;} "
                    "return out; }")
                print(f"  ancestor chain of the first reference: {chain}")
                block = await holder.evaluate(
                    "el => { let n=el; for (let i=0;i<4&&n.parentElement;i++) n=n.parentElement; "
                    "return n.outerHTML; }")
                print(f"  surrounding block HTML (first 3000 chars):\n{block[:3000]}")

            pag = page.locator("[class*='pag' i], [id*='pag' i], a:has-text('Next')")
            print(f"\n  pagination-like elements: {await pag.count()}")
            for i in range(min(await pag.count(), 6)):
                info = await pag.nth(i).evaluate(
                    "el => ({tag: el.tagName, id: el.id, cls: el.className, "
                    "text: el.textContent.trim().slice(0,50)})")
                print(f"    [{i}] {info}")

        print("\n" + "=" * 60)
        print("STEP 4: JSON responses the page fetched (possible data feed)")
        print("=" * 60)
        if not json_hits:
            print("  none seen")
        for status, url, ctype in json_hits[:15]:
            print(f"  {status} {ctype} {url}")

        await context.close()
        await browser.close()
    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
