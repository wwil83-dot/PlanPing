#!/usr/bin/env python3
"""
PlanFind — Northern Ireland shared Planning Portal reconnaissance tool
(2026-08-17).

One master portal (planningregister.planningsystemni.gov.uk, built on
TerraQuest's "TQ KeyChain" platform) covers 10 of NI's 11 councils —
everyone except Mid Ulster, which already has its own standard Idox
entry in idox_councils.py. If this one platform can be scraped, it's
full NI coverage in a single scraper — genuinely comparable in impact
to the original Arcus discovery.

CRITICAL DIFFERENCE FROM EVERY OTHER PLATFORM SO FAR: fetching the
search pages directly (plain HTTP, and even Playwright's page.content()
immediately after navigation) returns only an empty app shell —
"You need to enable JavaScript to run this app... Loading applications".
This is a client-side-rendered SPA, not a server-rendered form like
Idox/Northgate. That means the real data comes back over the network
as XHR/fetch calls AFTER the page's JS runs, not embedded in the
initial HTML. A plain requests/BeautifulSoup scraper (or even a
Playwright script that just reads page.content()) will not work here —
confirmed by direct fetch before writing this script, not assumed.

So this recon tool's real job is different from the others: rather than
inspecting static form markup, it opens a real browser, attaches a
network listener BEFORE navigating, and captures every XHR/fetch
response the app makes — real endpoint URLs, real request payloads,
real JSON response shapes — while performing an actual search. That's
the evidence a real scraper needs: the API this SPA calls, not the page
that renders around it.

Same "get real evidence before writing scraper code" discipline as
every other platform this project has onboarded. Nothing here assumes
field names, endpoint paths, or authority values in advance — all of it
is discovered live and dumped for direct inspection.
"""
import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

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

ADVANCED_SEARCH_URL = "https://planningregister.planningsystemni.gov.uk/advanced-search"

# Static asset extensions we don't care about — anything else that comes
# back over the network is worth inspecting, since we don't yet know
# what the real API host/path looks like (could be a different
# subdomain entirely — TerraQuest's other products often split the UI
# host from an api.* host, worth checking for explicitly).
SKIP_EXTENSIONS = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2",
    ".ico", ".gif", ".map",
)

captured: list[dict] = []


def _interesting(url: str) -> bool:
    path = urlparse(url).path.lower()
    if path.endswith(SKIP_EXTENSIONS):
        return False
    return True


async def _on_response(response):
    """Logs every non-static response. Real evidence, not a guess at
    which endpoint matters — we don't know the real API shape yet."""
    url = response.url
    if not _interesting(url):
        return
    try:
        content_type = response.headers.get("content-type", "")
    except Exception:
        content_type = ""
    entry = {
        "url": url,
        "status": response.status,
        "method": response.request.method,
        "content_type": content_type,
    }
    # Only try to read a body for likely-JSON/API responses — reading
    # the body of every response (including large HTML documents) would
    # slow this down and isn't the evidence we're after.
    if "json" in content_type.lower() or re.search(r"/api/|/graphql", url, re.I):
        try:
            body = await response.text()
            entry["body_preview"] = body[:4000]
            entry["body_length"] = len(body)
        except Exception as e:
            entry["body_error"] = str(e)
    captured.append(entry)
    print(f"    [NETWORK] {entry['method']} {response.status} {url}")


async def dump_dropdown_options(page, label_text: str):
    """Finds a <select> near a given visible label and lists its real
    option values/text — we need the real Authority option VALUES (not
    just the display names) to build a scraper that can filter per
    council, and there's no way to know these without inspecting the
    live DOM."""
    print(f"\n  Looking for a dropdown near label {label_text!r}...")
    try:
        # Advanced search fields are typically labelled <label> + <select>
        # pairs, but we don't know the real markup yet — try a few
        # reasonable strategies rather than assuming one.
        selects = page.locator("select")
        count = await selects.count()
        for i in range(count):
            sel = selects.nth(i)
            try:
                sel_id = await sel.get_attribute("id") or ""
                sel_name = await sel.get_attribute("name") or ""
                aria_label = await sel.get_attribute("aria-label") or ""
                haystack = f"{sel_id} {sel_name} {aria_label}".lower()
                if label_text.lower() not in haystack:
                    continue
                options = sel.locator("option")
                opt_count = await options.count()
                print(f"    Match: id={sel_id!r} name={sel_name!r} "
                      f"aria-label={aria_label!r} — {opt_count} options")
                for j in range(opt_count):
                    opt = options.nth(j)
                    val = await opt.get_attribute("value")
                    text = (await opt.inner_text()).strip()
                    print(f"      value={val!r} text={text!r}")
            except Exception as e:
                print(f"    (option {i} read error: {e})")
    except Exception as e:
        print(f"  ⚠ Dropdown search error: {e}")


async def try_click_dropdown_by_role(page, name_pattern: str):
    """Fallback for when the Authority field isn't a plain HTML <select>
    at all — modern SPAs (React/Vue/Angular component libraries) very
    often render a custom combobox with a div/button + a popup listbox,
    not a real <select>. Tries the accessible-role approach instead,
    which works regardless of the underlying markup, then dumps
    whatever real option text it finds."""
    print(f"\n  Trying ARIA role-based lookup for {name_pattern!r}...")
    try:
        combo = page.get_by_role("combobox", name=re.compile(name_pattern, re.I))
        if await combo.count() > 0:
            print(f"    Found {await combo.count()} combobox(es) matching {name_pattern!r}")
            await combo.first.click()
            await asyncio.sleep(1)
            options = page.get_by_role("option")
            opt_count = await options.count()
            print(f"    {opt_count} options visible after opening:")
            for j in range(min(opt_count, 30)):
                try:
                    text = (await options.nth(j).inner_text()).strip()
                    print(f"      [{j}] {text!r}")
                except Exception:
                    pass
            # close it again so it doesn't interfere with later steps
            await page.keyboard.press("Escape")
        else:
            print(f"    No ARIA combobox matched {name_pattern!r}")
    except Exception as e:
        print(f"    ⚠ ARIA lookup error: {e}")


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] NI Planning Portal recon\n")
    print(f"Target: {ADVANCED_SEARCH_URL}")
    print("Confirmed via direct fetch before this script was written: this")
    print("is a client-side-rendered SPA (TerraQuest TQ KeyChain), not a")
    print("server-rendered form. This script captures real network traffic")
    print("rather than reading static HTML.\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()
        page.on("response", lambda r: asyncio.create_task(_on_response(r)))

        # ------------------------------------------------------------
        # Step 1: load the advanced search page, let the SPA finish
        # its initial data load (participating-authorities list etc.)
        # ------------------------------------------------------------
        print("=" * 70)
        print("STEP 1: Load advanced search page")
        print("=" * 70)
        try:
            await page.goto(ADVANCED_SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"⚠ Navigation error: {e}")
            await browser.close()
            return

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            print("  (networkidle timeout — SPA may poll/long-poll; continuing anyway)")
        await asyncio.sleep(3)

        title = await page.title()
        print(f"\n  Real page title after JS render: {title!r}")

        with open("/tmp/ni_recon_step1_loaded.html", "w", encoding="utf-8") as f:
            f.write(await page.content())
        try:
            await page.screenshot(path="/tmp/ni_recon_step1_loaded.png", full_page=True)
        except Exception as e:
            print(f"  (screenshot failed: {e})")
        print("  Saved: /tmp/ni_recon_step1_loaded.html, .png")

        # ------------------------------------------------------------
        # Step 2: find and dump the real Authority dropdown — both as
        # a plain <select> and, if that fails, as an ARIA combobox,
        # since we don't know which the real markup uses yet.
        # ------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP 2: Inspect the Authority filter's real option values")
        print("=" * 70)
        await dump_dropdown_options(page, "authority")
        await try_click_dropdown_by_role(page, "authority")

        # Also dump ALL selects/comboboxes regardless of label match, in
        # case our label-guessing above missed the real one — better to
        # over-collect than to miss the one field that actually matters.
        print("\n  All <select> elements on the page (unfiltered):")
        selects = page.locator("select")
        sel_count = await selects.count()
        print(f"  Total <select> count: {sel_count}")
        for i in range(sel_count):
            sel = selects.nth(i)
            try:
                sel_id = await sel.get_attribute("id") or "(no id)"
                sel_name = await sel.get_attribute("name") or "(no name)"
                opt_count = await sel.locator("option").count()
                print(f"    [{i}] id={sel_id!r} name={sel_name!r} options={opt_count}")
            except Exception:
                pass

        print("\n  All elements with role=combobox (unfiltered):")
        try:
            combos = page.get_by_role("combobox")
            combo_count = await combos.count()
            print(f"  Total combobox count: {combo_count}")
            for i in range(min(combo_count, 15)):
                try:
                    acc_name = await combos.nth(i).get_attribute("aria-label") or "(no aria-label)"
                    print(f"    [{i}] aria-label={acc_name!r}")
                except Exception:
                    pass
        except Exception as e:
            print(f"  (combobox enumeration error: {e})")

        # ------------------------------------------------------------
        # Step 3: run one real, broad search — "last month", no
        # authority filter — to see the real results list structure
        # and (critically) the real API call it triggers.
        # ------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP 3: Run a real search — 'Last month', all authorities")
        print("=" * 70)
        try:
            last_month = page.get_by_text(re.compile(r"^Last month$", re.I))
            if await last_month.count() > 0:
                await last_month.first.click()
                print("  Clicked 'Last month' quick filter")
            else:
                print("  ⚠ 'Last month' quick filter not found by text — "
                      "real markup may differ from the screenshot; check "
                      "the saved HTML/screenshot for the real label")
        except Exception as e:
            print(f"  ⚠ Could not click 'Last month': {e}")

        await asyncio.sleep(1)

        try:
            search_btn = page.get_by_role("button", name=re.compile(r"^Search$", re.I))
            if await search_btn.count() > 0:
                captured.clear()  # only care about what THIS search triggers
                print("  Clicking Search — capturing network traffic from here...")
                await search_btn.first.click()
            else:
                print("  ⚠ Search button not found by role/name — check saved HTML")
        except Exception as e:
            print(f"  ⚠ Could not click Search: {e}")

        try:
            await page.wait_for_load_state("networkidle", timeout=25_000)
        except PlaywrightTimeout:
            print("  (networkidle timeout after search — continuing anyway)")
        await asyncio.sleep(3)

        with open("/tmp/ni_recon_step3_results.html", "w", encoding="utf-8") as f:
            f.write(await page.content())
        try:
            await page.screenshot(path="/tmp/ni_recon_step3_results.png", full_page=True)
        except Exception as e:
            print(f"  (screenshot failed: {e})")
        print("  Saved: /tmp/ni_recon_step3_results.html, .png")

        try:
            body_text = await page.locator("body").inner_text()
            snippet = " ".join(body_text.split())[:800]
            print(f"\n  Visible body text after search (first 800 chars):\n  {snippet!r}")
        except Exception as e:
            print(f"  (couldn't extract body text: {e})")

        # ------------------------------------------------------------
        # Step 4: try to open ONE individual application's detail view,
        # to capture whatever additional API call that triggers (often
        # a separate, more detailed endpoint than the list view).
        # ------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP 4: Open one individual application (if any results)")
        print("=" * 70)
        try:
            detail_links = page.get_by_text(re.compile(r"details|view", re.I))
            dl_count = await detail_links.count()
            print(f"  Candidate 'details/view' elements found: {dl_count}")
            if dl_count > 0:
                captured.clear()
                await detail_links.first.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                except PlaywrightTimeout:
                    pass
                await asyncio.sleep(2)
                with open("/tmp/ni_recon_step4_detail.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
                try:
                    await page.screenshot(path="/tmp/ni_recon_step4_detail.png", full_page=True)
                except Exception:
                    pass
                print("  Saved: /tmp/ni_recon_step4_detail.html, .png")
                print(f"  Current URL after click: {page.url}")
            else:
                print("  No obvious detail link found — check the results "
                      "screenshot from Step 3 to see the real results layout")
        except Exception as e:
            print(f"  ⚠ Detail-view step error: {e}")

        await browser.close()

    # ------------------------------------------------------------
    # Dump every captured network call to a JSON file for full
    # offline inspection — this is the real evidence a scraper needs.
    # ------------------------------------------------------------
    with open("/tmp/ni_recon_network_capture.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2)

    print("\n" + "=" * 70)
    print("RECON COMPLETE")
    print("=" * 70)
    print(f"Captured {len(captured)} non-static network responses total "
          f"(from Step 4 onward — earlier steps' captures were cleared "
          f"deliberately to isolate what each action actually triggers).")
    print("\nFiles saved to /tmp/ (download the workflow artifact if running")
    print("via GitHub Actions):")
    print("  ni_recon_step1_loaded.html / .png   — initial page load")
    print("  ni_recon_step3_results.html / .png  — after running a search")
    print("  ni_recon_step4_detail.html / .png   — after opening one application")
    print("  ni_recon_network_capture.json       — every real API call from")
    print("                                         Step 4 (detail view)")
    print("\nRead the printed [NETWORK] lines above for the Step 1-3 calls —")
    print("particularly the ones with a JSON content-type, since that's the")
    print("real API this SPA is built on. Before writing any scraper code:")
    print("  1. Confirm the real API host/path (may not be the same domain")
    print("     as the page URL — check for an api.* or similar subdomain).")
    print("  2. Confirm whether Authority filtering happens via a request")
    print("     parameter (ideal — means we can hit it directly) or purely")
    print("     client-side (means every scrape needs the full unfiltered")
    print("     result set filtered locally).")
    print("  3. Check whether the API needs a session/auth token from the")
    print("     page load, or accepts anonymous requests directly — this")
    print("     determines whether a lightweight requests-based scraper is")
    print("     possible at all, or whether Playwright is required just to")
    print("     get a valid token before every real data pull.")


if __name__ == "__main__":
    asyncio.run(main())
