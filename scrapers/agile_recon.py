#!/usr/bin/env python3
"""
PlanFind — Mole Valley RECON, round 2 (writes NOTHING to the database).

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
    print("=" * 78)
    print("STATMAP HORIZON PORTAL:", STATMAP)
    page = await ctx.new_page()
    watch(page, "api")
    await page.goto(STATMAP, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(8)
    await click_visible(page, "Accept additional cookies")
    await asyncio.sleep(2)

    for tab in ("Weekly Lists", "Planning Applications"):
        print(f"\n>>> TAB: {tab}")
        if await click_visible(page, tab):
            await asyncio.sleep(6)
            await describe(page, f"after opening {tab}")
            # Press the Search / Go button if present, to see the data call.
            for btn in ("Search", "Go to Search", "Show results", "Submit"):
                if await click_visible(page, btn):
                    await asyncio.sleep(10)
                    await describe(page, f"after {btn} on {tab}")
                    break
    await page.close()


async def agile(ctx):
    print("=" * 78)
    print("AGILE APPLICATIONS (why is it blank?):", AGILE)
    page = await ctx.new_page()
    watch(page, "agile-xhr", body_len=500)
    page.on("console", lambda m: print(f"  [console.{m.type}] {short(m.text, 300)}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: print(f"  [pageerror] {short(str(e), 400)}"))
    page.on("requestfailed", lambda r: print(f"  [requestfailed] {r.url[:160]} {r.failure}"))
    await page.goto(AGILE, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(30)
    await describe(page, "Agile after 30s")
    await page.close()


async def main():
    print("PlanFind Mole Valley recon, round 2 (no database writes)")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London",
                                        viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
        for fn in (statmap, agile):
            try:
                await fn(ctx)
            except Exception as e:
                print("  ✗", fn.__name__, "failed:", short(str(e), 300))
        await browser.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
