"""Idox stale councils, round 2 (writes nothing).

Round 1 (idox_stale7_probe): the Chrome-fingerprint client (curl_cffi) gets the real search form from the three Cloudflare
councils (North East Derbyshire, Bolsover, Derbyshire Dales); Blaby and Hinckley load fine for everyone; Gosport and Melton time
out from the runner (they load from a home connection with the VPN off).

Part A  Blaby + Hinckley: replay the nightly scraper's flow in Playwright (monthlyList page -> pick month / date-received radio
        -> submit) and report what is on the page afterwards ('Results timeout - title Monthly List' is what the scraper sees).
        Also tries weeklyListResults / monthlyListResults ?action=firstPage and an advanced search.
Part B  NE Derbyshire + Bolsover + Derbyshire Dales (+ Bradford): with curl_cffi, load the form, keep cookies, read the _csrf token,
        then try list pages and an advanced date-received search (POST) and say whether real results come back.
"""
import asyncio
import re
import sys
from datetime import date, timedelta

COUNCILS_A = {
    "Blaby": "https://pa.blaby.gov.uk/online-applications",
    "Hinckley & Bosworth": "https://pa.hinckley-bosworth.gov.uk/online-applications",
}
COUNCILS_B = {
    "NE Derbyshire": "https://planapps-online.ne-derbyshire.gov.uk/online-applications",
    "Bolsover": "https://publicaccess.bolsover.gov.uk/online-applications",
    "Derbyshire Dales": "https://planning.derbyshiredales.gov.uk/online-applications",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def flat(s, n=400):
    return re.sub(r"\s+", " ", s or "").strip()[:n]


def results_in(html):
    return len(re.findall(r'class="searchresult"|<li class="searchresult', html))


# ---------------------------------------------------------------- Part A
async def part_a():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        br = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for name, base in COUNCILS_A.items():
            print(f"\n== A: {name}")
            ctx = await br.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London")
            page = await ctx.new_page()
            seen = []
            page.on("response", lambda r: seen.append((r.status, r.url[-90:])) if "online-applications" in r.url and "." not in r.url.rsplit("/", 1)[-1].split("?")[0][-4:] else None)
            try:
                await page.goto(f"{base}/search.do?action=monthlyList&searchCriteria.monthYearIndex=0&searchType=Application",
                                wait_until="domcontentloaded", timeout=45000)
                print(f"  monthlyList: title={await page.title()!r} url={page.url[-80:]}")
                form = await page.evaluate("""() => { const f = document.querySelector('#monthlyListForm') || document.querySelector('form');
                    if (!f) return 'NO FORM'; return f.outerHTML.slice(0, 2500); }""")
                print(f"  form html: {flat(form, 1800)}")
                sels = await page.evaluate("""() => [...document.querySelectorAll('select')].map(s => ({id: s.id, name: s.name,
                    options: [...s.options].slice(0, 4).map(o => o.value + '=' + o.text.trim())}))""")
                print(f"  selects: {sels}")
                radios = await page.evaluate("""() => [...document.querySelectorAll('input[type=radio]')].map(r => ({id: r.id, name: r.name, value: r.value, checked: r.checked}))""")
                print(f"  radios: {radios}")
                subs = await page.evaluate("""() => [...document.querySelectorAll('input[type=submit],button')].map(b => ({tag: b.tagName, id: b.id, value: b.value || b.textContent.trim().slice(0, 30), cls: b.className}))""")
                print(f"  submit controls: {subs}")
                body0 = flat(await page.inner_text("body"), 500)
                print(f"  body: {body0}")
                # replay: radio then submit
                for sel in ["input#dateReceived", "input[value='dateReceived']", "input[value='dc']", "input[value='DC']"]:
                    if await page.locator(sel).count():
                        await page.locator(sel).first.click()
                        print(f"  clicked radio {sel}")
                        break
                for sel in ["#monthlyListForm input[type='submit']", "form input[type='submit']", "form button[type='submit']", "input.button"]:
                    if await page.locator(sel).count():
                        await page.locator(sel).first.click()
                        print(f"  clicked submit {sel}")
                        break
                try:
                    await page.wait_for_selector("ul.searchresults, #searchResultsContainer, .searchresults, .no-results, #searchResultsForm", timeout=25000)
                    print("  RESULTS CONTAINER APPEARED")
                except Exception:
                    print("  results container did NOT appear")
                print(f"  after submit: title={await page.title()!r} url={page.url[-90:]}")
                print(f"  body: {flat(await page.inner_text('body'), 700)}")
                err = await page.evaluate("""() => [...document.querySelectorAll('.error,.errors,.messagebox,[role=alert],.validation')].map(e => e.textContent.trim().slice(0, 160))""")
                print(f"  error boxes: {err}")
            except Exception as e:
                print(f"  ERROR {type(e).__name__}: {str(e)[:200]}")
            for path in ("/monthlyListResults.do?action=firstPage", "/weeklyListResults.do?action=firstPage"):
                try:
                    await page.goto(base + path, wait_until="domcontentloaded", timeout=30000)
                    html = await page.content()
                    print(f"  {path}: title={await page.title()!r} results={results_in(html)} body={flat(await page.inner_text('body'), 160)!r}")
                except Exception as e:
                    print(f"  {path}: ERROR {type(e).__name__}: {str(e)[:120]}")
            await ctx.close()
        await br.close()


# ---------------------------------------------------------------- Part B
def part_b():
    from curl_cffi import requests as cr
    today = date.today()
    frm, to = (today - timedelta(days=7)).strftime("%d/%m/%Y"), today.strftime("%d/%m/%Y")
    for name, base in COUNCILS_B.items():
        print(f"\n== B: {name}")
        s = cr.Session(impersonate="chrome")
        try:
            r = s.get(f"{base}/search.do?action=advanced&searchType=Application", timeout=40)
            print(f"  form: HTTP {r.status_code} title={flat(re.search(r'<title[^>]*>(.*?)</title>', r.text, re.I | re.S).group(1) if re.search(r'<title', r.text, re.I) else '', 60)!r} cookies={list(s.cookies.keys())}")
            m = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text) or re.search(r'value="([^"]+)"\s+name="_csrf"', r.text)
            token = m.group(1) if m else None
            print(f"  csrf token: {'found' if token else 'NOT FOUND'}")
        except Exception as e:
            print(f"  form ERROR {type(e).__name__}: {str(e)[:150]}")
            continue
        for path in ("/monthlyListResults.do?action=firstPage", "/weeklyListResults.do?action=firstPage"):
            try:
                r = s.get(base + path, timeout=40)
                print(f"  GET {path}: HTTP {r.status_code} results={results_in(r.text)} title={flat(re.search(r'<title[^>]*>(.*?)</title>', r.text, re.I | re.S).group(1) if re.search(r'<title', r.text, re.I) else '', 50)!r}")
            except Exception as e:
                print(f"  GET {path}: ERROR {str(e)[:120]}")
        data = {"searchCriteria.caseStatus": "", "date(applicationReceivedStart)": frm, "date(applicationReceivedEnd)": to,
                "searchType": "Application"}
        if token:
            data["_csrf"] = token
        try:
            r = s.post(f"{base}/advancedSearchResults.do?action=firstPage", data=data, timeout=60,
                       headers={"Referer": f"{base}/search.do?action=advanced&searchType=Application"})
            t = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
            print(f"  POST advanced {frm}..{to}: HTTP {r.status_code} results on page={results_in(r.text)} title={flat(t.group(1) if t else '', 60)!r}")
            if r.status_code != 200 or not results_in(r.text):
                print(f"    body: {flat(re.sub(r'<script.*?</script>', '', r.text, flags=re.S | re.I).replace('<', ' <'), 300)}")
        except Exception as e:
            print(f"  POST ERROR {type(e).__name__}: {str(e)[:150]}")


def main():
    part_b()
    asyncio.run(part_a())
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main() or 0)
