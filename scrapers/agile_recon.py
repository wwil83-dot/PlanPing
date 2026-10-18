#!/usr/bin/env python3
"""
PlanFind — Mole Valley RECON, round 3 (writes NOTHING to the database).

Round 1 showed:
  - planning.agileapplications.co.uk/mole loads its framework and the council
    config (Mole Valley, id 328) but then renders a blank page.
  - molevalley-publicportal.statmap.co.uk/horizonext (Statmap "Mirage") renders,
    with tabs: Property Search / Planning Applications / Planning Appeals /
    Weekly Lists, and talks to /horizoNext/api/mirage/...

This round drives the Statmap portal like a person: accept cookies, open
"Weekly Lists", then "Planning Applications", press Search, and log every
API call (method, URL, request body, status, start of reply) so the scraper
can call the data endpoint directly. It also logs why the Agile page is blank
(console errors, failed requests).

Output is the log only. Send the whole log back.
"""
import asyncio, re
from playwright.async_api import async_playwright

STATMAP = "https://molevalley-publicportal.statmap.co.uk/horizonext"
AGILE = "https://planning.agileapplications.co.uk/mole"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ttf", ".css", ".ico", ".map", ".js")
SKIP_HOSTS = ("google", "doubleclick", "facebook", "hotjar", "clarity.ms", "unpkg.com",
              "cdnjs.cloudflare.com", "tile.openstreetmap", "arcgis")


def short(s, n=700):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:n] + ("…" if len(s) > n else "")


def watch(page, tag, body_len=1800):
    seen = set()

    async def on_response(resp):
        u = resp.url
        if u.lower().split("?")[0].endswith(SKIP_EXT) or any(h in u for h in SKIP_HOSTS):
            return
        if resp.request.resource_type not in ("xhr", "fetch"):
            return
        req = resp.request
        key = (req.method, u, req.post_data)
        if key in seen:
            return
        seen.add(key)
        ct = resp.headers.get("content-type", "")
        try:
            body = short(await resp.text(), body_len) if ("json" in ct or "text" in ct or "xml" in ct) else f"({ct})"
        except Exception:
            body = "(body unavailable)"
        hdrs = {k: v for k, v in req.headers.items()
                if k.lower() not in ("cookie", "user-agent", "accept-language", "accept-encoding",
                                     "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "referer",
                                     "origin", "connection", "host")}
        print(f"  [{tag}] {req.method} {resp.status} {u}")
        if req.post_data:
            print(f"      REQUEST BODY: {short(req.post_data, 900)}")
        print(f"      REQUEST HEADERS: {hdrs}")
        print(f"      REPLY: {body}")

    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))


async def describe(page, label):
    print(f"\n  ---- {label} ----")
    print("  URL:", page.url)
    try:
        print("  TEXT:", short(await page.inner_text("body"), 1400))
    except Exception as e:
        print("  (no text)", short(str(e), 100))
    try:
        fields = await page.eval_on_selector_all(
            "input, select, textarea",
            "els => els.filter(e => e.offsetParent !== null).map(e => ({t:e.type||e.tagName, n:e.name||e.id||'', p:e.placeholder||'', l:(e.getAttribute('aria-label')||'')})).slice(0,40)")
        print("  VISIBLE FIELDS:", fields)
        btns = await page.eval_on_selector_all(
            "button, [role=button], [role=tab], a.nav-link",
            "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText||e.value||e.getAttribute('aria-label')||'').trim().slice(0,50)).filter(Boolean).slice(0,40)")
        print("  VISIBLE BUTTONS/TABS:", btns)
    except Exception as e:
        print("  (field scan failed)", short(str(e), 100))


async def click_visible(page, name, exact=True):
    pat = re.compile(rf"^\s*{re.escape(name)}\s*$" if exact else re.escape(name), re.I)
    for role in ("tab", "button", "link"):
        loc = page.get_by_role(role, name=pat)
        n = await loc.count()
        for i in range(n):
            el = loc.nth(i)
            if await el.is_visible():
                await el.click(timeout=8_000)
                print(f"  → clicked {role} {name!r}")
                return True
    print(f"  ✗ no visible tab/button/link named {name!r}")
    return False


async def statmap(ctx):
    """Round 2 showed Weekly Lists is stale (5 PDFs, last 2026-05-29) and the
    applications tab vanished after leaving the start page. So: do the
    Planning Applications search FIRST, with and without a date filter, page
    forward once, and open one result, logging every API call."""
    from datetime import date, timedelta
    print("=" * 78)
    print("STATMAP HORIZON PORTAL (applications search):", STATMAP)
    page = await ctx.new_page()
    watch(page, "api", body_len=2500)
    await page.goto(STATMAP, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(8)
    await click_visible(page, "Accept additional cookies")
    await asyncio.sleep(2)

    if not await click_visible(page, "Planning Applications"):
        await describe(page, "start page (tab not found)")
        await page.close()
        return
    await asyncio.sleep(5)
    await describe(page, "Planning Applications search form")

    # ---- Search 1: date-received range, last 14 days ----
    today = date.today()
    d_from, d_to = (today - timedelta(days=14)).strftime("%d/%m/%Y"), today.strftime("%d/%m/%Y")
    inputs = page.locator("input[type=text]")
    n = await inputs.count()
    filled = []
    for i in range(n):
        el = inputs.nth(i)
        try:
            if not await el.is_visible():
                continue
            name = ((await el.get_attribute("name")) or "") + " " + ((await el.get_attribute("id")) or "")
            low = name.lower()
            if "receiv" in low and "from" in low:
                await el.fill(d_from); filled.append((name.strip(), d_from))
            elif "receiv" in low and "to" in low:
                await el.fill(d_to); filled.append((name.strip(), d_to))
        except Exception:
            continue
    print("  filled date fields:", filled or "NONE MATCHED (see VISIBLE FIELDS above)")
    await asyncio.sleep(1)
    if await click_visible(page, "Search"):
        await asyncio.sleep(10)
        await describe(page, "results after date-range search")

        # ---- page forward once (shows pageSize/offset handling) ----
        if await click_visible(page, "next", exact=False):
            await asyncio.sleep(6)
            await describe(page, "results, page 2")

        # ---- open the first result (shows the detail call + detail fields) ----
        rows = page.locator("table tbody tr a, table tbody tr")
        try:
            if await rows.count() > 0:
                print("\n>>> opening first result")
                await rows.first.click(timeout=8_000)
                await asyncio.sleep(8)
                await describe(page, "first result detail")
        except Exception as e:
            print("  (could not open a result)", short(str(e), 150))
    await page.close()


async def main():
    print("PlanFind Mole Valley recon, round 3 (no database writes)")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London",
                                        viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
        for fn in (statmap,):
            try:
                await fn(ctx)
            except Exception as e:
                print("  ✗", fn.__name__, "failed:", short(str(e), 300))
        await browser.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
