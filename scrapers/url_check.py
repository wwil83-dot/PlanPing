#!/usr/bin/env python3
"""
PlanFind - URL REACHABILITY CHECK (writes NOTHING to the database).
Env URLS = comma-separated. For each URL: plain HTTP (status, server, title) then headless Chromium
(final URL, title, first 200 chars of text). Used to find a route into hosts that return CloudFront 403.
"""
import asyncio
import os
import re
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
URLS = [u.strip() for u in os.environ.get("URLS", "").split(",") if u.strip()]


def plain(u):
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                                                 "Accept-Language": "en-GB,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read().decode("utf-8", "replace")
            return r.status, r.headers.get("Server"), re.sub(r"\s+", " ", (re.search(r"<title>(.*?)</title>", b, re.S | re.I) or [0, ""])[1])[:80], len(b)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Server"), "", 0
    except Exception as e:
        return "ERR", str(e)[:70], "", 0


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(user_agent=UA, locale="en-GB", ignore_https_errors=True)
        for u in URLS:
            print(f"\n=== {u}")
            print("  plain :", plain(u))
            pg = await ctx.new_page()
            try:
                r = await pg.goto(u, wait_until="networkidle", timeout=45000)
                txt = (await pg.inner_text("body"))[:200].replace("\n", " ")
                print("  chrome:", r.status if r else None, pg.url[:100], "|", (await pg.title())[:70], "|", txt)
            except Exception as e:
                print("  chrome: ERR", str(e)[:120])
            await pg.close()
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
