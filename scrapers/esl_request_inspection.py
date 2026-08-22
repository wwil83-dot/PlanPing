#!/usr/bin/env python3
"""
PlanFind — Eden/South Lakeland real request inspection (2026-08-22).

Real, confirmed: 4 different guessed query-string patterns all
returned identical results to page 1 — the Results page is almost
certainly session-state-based (ignores unrecognized query params,
just re-serves whatever the last real search produced), not simple
GET pagination. Directly intercepting the real network request the
search form actually makes on submit — its real method, real
parameters, and any real hidden fields — rather than guessing further
URL patterns blind.
"""
import asyncio
from datetime import date, timedelta, datetime, timezone

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

captured_requests = []


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Eden/South Lakeland real request inspection\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        print(f"Chromium launched: {browser.version}\n")
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        def on_request(request):
            if "Search" in request.url or "Results" in request.url:
                captured_requests.append({
                    "method": request.method,
                    "url": request.url,
                    "post_data": request.post_data,
                })

        page.on("request", on_request)

        await page.goto("https://planningregister.westmorlandandfurness.gov.uk/Search/Advanced",
                         wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass

        # Real, direct check of the search FORM's own real attributes
        # before submitting anything
        form_info = await page.evaluate("""() => {
            const forms = document.querySelectorAll('form');
            return Array.from(forms).map(f => ({
                action: f.action, method: f.method, id: f.id,
            }));
        }""")
        print(f"Real <form> elements on the Advanced Search page:")
        for f in form_info:
            print(f"  action={f['action']!r} method={f['method']!r} id={f['id']!r}")

        # Real, direct check for ANY hidden input containing "page",
        # "skip", "take", "size" in its name — the kind of field a
        # real ASP.NET MVC app might use for server-side paging
        hidden_fields = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type=hidden]');
            return Array.from(inputs).map(i => ({name: i.name, value: i.value}));
        }""")
        print(f"\nReal hidden fields on the Advanced Search page ({len(hidden_fields)} total):")
        for h in hidden_fields:
            print(f"  name={h['name']!r} value={h['value'][:60]!r}")

        today = date.today()
        start = today - timedelta(days=30)
        await page.fill("#DateReceivedFrom", start.strftime("%d/%m/%Y"), timeout=5_000)
        await page.fill("#DateReceivedTo", today.strftime("%d/%m/%Y"), timeout=5_000)

        print(f"\nSubmitting real search now — capturing the real request made...\n")
        await page.locator("button:has-text('Search')").first.click(timeout=5_000)

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)

        print(f"Real captured requests to Search/Results-related URLs:")
        for r in captured_requests:
            print(f"\n  {r['method']} {r['url']}")
            if r['post_data']:
                print(f"    Real POST data: {r['post_data'][:500]}")

        # Real, direct check for hidden fields / pagination controls on
        # the RESULTS page itself now that we're there
        results_hidden = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type=hidden]');
            return Array.from(inputs).map(i => ({name: i.name, value: i.value}));
        }""")
        print(f"\n\nReal hidden fields on the RESULTS page ({len(results_hidden)} total):")
        for h in results_hidden:
            print(f"  name={h['name']!r} value={h['value'][:60]!r}")

        # Real, direct check for ANY element (not just <a>/<button>)
        # with real text suggesting pagination, anywhere on the page
        pagination_hints = await page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            const hits = [];
            for (const el of all) {
                const text = el.textContent || '';
                if (el.children.length === 0 && /next|page \\d|show more|load more/i.test(text) && text.length < 40) {
                    hits.push({tag: el.tagName, text: text.trim(), class: el.className});
                }
            }
            return hits.slice(0, 20);
        }""")
        print(f"\nReal elements with pagination-suggestive text: {len(pagination_hints)}")
        for h in pagination_hints:
            print(f"  <{h['tag']}> class={h['class']!r} text={h['text']!r}")

        await context.close()
        await browser.close()

    print(f"\n{'=' * 70}")
    print("INSPECTION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
