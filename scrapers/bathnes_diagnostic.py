#!/usr/bin/env python3
"""
Bath & North East Somerset weekly list diagnostic (2026-10-04, rev 4).

CONFIRMED from rev 3's real output:
  - select#weeklyListOption (Validated/Decided) and
    select#weeklyListBetween (9 weeks; the FIRST is the coming week,
    05/10/2026 to 11/10/2026, so it is empty by design)
  - the weekly list's own Search control is button#weeklySearchBtn
  - clicking it makes the page POST
      https://api.bathnes.gov.uk/webapi/api/PlanningAPI/v2/planningdata/search/
    with a body like {"application_isharedate_from":"2026-10-05",
    "application_isharedate_to":"2026-10-11"}; the response was a JSON
    array ('[]' for the empty coming week)
  - rev 3's later combinations all failed with "select not visible":
    after the first search the URL stayed at ...#weeklyList, and a
    goto to a URL differing only by hash is a same-document jump —
    no reload, so the results view stayed on screen. Each combination
    here uses a FRESH page instead.

NOTE: api.bathnes.gov.uk disallows automated access in its robots.txt.
This script does not request the API itself; it only observes the
responses the page receives when its own Search button is pressed.

This revision captures, for the two most recent COMPLETE weeks x
Validated/Decided: the request body the page sends (the Decided body
may use different field names), and the full JSON response shape —
count, keys, and one real example value per key.
"""
import asyncio
import json
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


def summarize(records):
    """Shape of a JSON response: count, union of keys, one example per key."""
    if not isinstance(records, list):
        return {"type": type(records).__name__,
                "keys": list(records)[:20] if isinstance(records, dict) else None}
    keys, examples = [], {}
    for r in records:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if k not in keys:
                keys.append(k)
            if k not in examples or (examples[k] in (None, "") and v not in (None, "")):
                examples[k] = v
    return {"count": len(records), "keys": keys,
            "examples": {k: str(v)[:90] for k, v in examples.items()}}


async def settle(page, extra=1.5):
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(extra)


async def run_combo(context, week_pos, status_word):
    page = await context.new_page()   # fresh page: avoids the hash-only no-reload trap
    captured = []

    async def on_response(resp):
        try:
            if "api.bathnes.gov.uk" not in resp.url or "OSHubToken" in resp.url:
                return
            if "json" not in resp.headers.get("content-type", "").lower():
                return
            captured.append({"url": resp.url, "method": resp.request.method,
                             "post_data": resp.request.post_data or "",
                             "status": resp.status, "text": await resp.text()})
        except Exception:
            pass

    page.on("response", on_response)
    out = {"week_pos": week_pos, "status": status_word}
    try:
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        await settle(page)

        status = page.locator("select#weeklyListOption")
        week = page.locator("select#weeklyListBetween")
        await status.select_option(label=status_word, timeout=8_000)
        await settle(page, 1.5)

        options = [o.strip() for o in await week.locator("option").all_text_contents()]
        out["week_options_total"] = len(options)
        if week_pos >= len(options):
            out["error"] = f"only {len(options)} week options"
            return out
        await week.select_option(index=week_pos, timeout=8_000)
        out["week_label"] = options[week_pos]
        await settle(page, 1.5)

        captured.clear()   # only keep calls made by the Search press itself
        await page.locator("button#weeklySearchBtn").click(timeout=8_000)
        await settle(page, 3)

        out["calls"] = []
        for c in captured:
            entry = {"method": c["method"], "status": c["status"],
                     "url": c["url"][:160], "request_body": c["post_data"][:300]}
            try:
                entry["shape"] = summarize(json.loads(c["text"]))
            except Exception:
                entry["raw_start"] = c["text"][:200]
            out["calls"].append(entry)

        body = await page.locator("body").inner_text()
        out["ui_says_no_results"] = "No results found" in body
        out["ui_body_start"] = body[:400]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        await page.close()
    return out


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)

        print("=" * 60)
        print("Two most recent complete weeks x Validated / Decided")
        print("=" * 60)
        for status_word in ("Validated", "Decided"):
            for pos in (1, 2):
                r = await run_combo(context, pos, status_word)
                print(f"\n--- {status_word}, week option #{pos}: {r.get('week_label')} "
                      f"(options in dropdown: {r.get('week_options_total')}) ---")
                if "error" in r:
                    print(f"  ERROR: {r['error']}")
                    continue
                print(f"  UI says 'No results found': {r['ui_says_no_results']}")
                for c in r["calls"]:
                    print(f"  {c['method']} {c['status']} {c['url']}")
                    if c["request_body"]:
                        print(f"    request body: {c['request_body']}")
                    if "shape" in c:
                        sh = c["shape"]
                        print(f"    count: {sh.get('count')}  keys: {sh.get('keys')}")
                        for k, v in (sh.get("examples") or {}).items():
                            print(f"      {k}: {v!r}")
                    else:
                        print(f"    raw: {c.get('raw_start')!r}")
                if not any((c.get("shape") or {}).get("count") for c in r["calls"]):
                    print(f"  page text start: {r['ui_body_start']!r}")

        await context.close()
        await browser.close()
    print("\nDiagnostic complete.")


if __name__ == "__main__":
    asyncio.run(main())
