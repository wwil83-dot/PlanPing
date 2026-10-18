#!/usr/bin/env python3
"""
PlanFind — Agile Applications / Mole Valley RECON (writes NOTHING to the database).

Mole Valley's site says its "real-time weekly lists" live at
planning.agileapplications.co.uk/mole, and its search at a Statmap "Horizon"
portal. I have not been able to load either from the build environment, so this
script finds out, from the UK runner, how they actually work:

  - which page URLs load, with the visible text and the links/buttons on them
  - EVERY background (XHR/fetch) request the page makes while loading and while
    we poke at it: URL, method, status and the start of the JSON reply
    -> these are what a scraper should call directly, instead of clicking

Output is the log only. Send the whole log back.

Env: RECON_URLS (comma separated, optional) overrides the default URL list.
"""
import asyncio, json, os, re, sys
from playwright.async_api import async_playwright

DEFAULT_URLS = [
    "https://planning.agileapplications.co.uk/mole",
    "https://planning.agileapplications.co.uk/mole/search-applications",
    "https://molevalley-publicportal.statmap.co.uk/horizonext",
]
URLS = [u.strip() for u in os.environ.get("RECON_URLS", "").split(",") if u.strip()] or DEFAULT_URLS

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ttf", ".css", ".ico", ".map")
SKIP_HOSTS = ("google", "googletagmanager", "doubleclick", "facebook", "hotjar", "clarity.ms")


def short(s: str, n: int = 700) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:n] + ("…" if len(s) > n else "")


async def run():
    print("PlanFind Agile / Mole Valley recon (no database writes)\n")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London",
                                        viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
        for url in URLS:
            print("=" * 78)
            print("URL:", url)
            page = await ctx.new_page()
            seen: list[str] = []

            async def on_response(resp, seen=seen):
                u = resp.url
                low = u.lower().split("?")[0]
                if low.endswith(SKIP_EXT) or any(h in u for h in SKIP_HOSTS):
                    return
                rt = resp.request.resource_type
                if rt not in ("xhr", "fetch", "document"):
                    return
                ct = resp.headers.get("content-type", "")
                body = ""
                try:
                    if "json" in ct or "text" in ct or "xml" in ct:
                        body = short(await resp.text(), 600)
                except Exception:
                    body = "(body unavailable)"
                line = f"  [{rt}] {resp.request.method} {resp.status} {u}\n      type={ct}\n      body={body}"
                if line not in seen:
                    seen.append(line)
                    print(line)

            page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                print("  status:", resp.status if resp else None, "| final URL:", page.url)
                await asyncio.sleep(8)
                try:
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                except Exception:
                    pass
                print("  title:", await page.title())
                text = await page.inner_text("body")
                print("  VISIBLE TEXT:", short(text, 1500))
                links = await page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => [e.innerText.trim().slice(0,60), e.href]).slice(0,60)")
                print(f"  LINKS ({len(links)}):")
                for t, h in links:
                    print("    ", repr(t), "->", h)
                btns = await page.eval_on_selector_all(
                    "button, [role=button], [role=tab], input[type=submit]",
                    "els => els.map(e => (e.innerText||e.value||e.getAttribute('aria-label')||'').trim().slice(0,60)).filter(Boolean).slice(0,40)")
                print("  BUTTONS/TABS:", btns)

                # Poke: anything that looks like a weekly list / search entry point.
                for label in ("weekly list", "Weekly", "Search", "Applications received", "Decided"):
                    try:
                        el = page.get_by_text(re.compile(label, re.I)).first
                        if await el.count() > 0 and await el.is_visible():
                            print(f"  → clicking text matching {label!r}")
                            await el.click(timeout=5_000)
                            await asyncio.sleep(6)
                            print("     now at:", page.url)
                            print("     TEXT:", short(await page.inner_text("body"), 900))
                            break
                    except Exception as e:
                        print(f"     (click {label!r} failed: {short(str(e), 120)})")
            except Exception as e:
                print("  ✗ failed:", short(str(e), 300))
            await page.close()
        await browser.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(run())
