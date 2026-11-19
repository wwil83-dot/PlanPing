"""
wealden_recon.py - READ-ONLY recon of Wealden's new planning pages (planning.wealden.gov.uk). Writes nothing.

1. Fetches /Planning/WeeklyList/ and prints every link (so we see how each week's 'valid' / 'determined' lists are addressed).
2. Follows the first 'received valid' and first 'determined' link: prints final URL, status, table headers, row count,
   the first 2 rows, and any pagination / next-page links.
3. Tries /Search/Results and /Search directly, and prints the search form fields (names, types, options).
"""
import re, sys, json
import httpx
from bs4 import BeautifulSoup

BASE = "https://planning.wealden.gov.uk"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
     "Accept-Language": "en-GB,en;q=0.9"}


def show_page(label, r):
    print(f"\n=== {label}: {r.request.method} {r.url}  -> {r.status_code}  type={r.headers.get('content-type')}  len={len(r.content)}")
    if "pdf" in (r.headers.get("content-type") or "").lower():
        print("   PDF response (first bytes):", r.content[:20]); return None
    soup = BeautifulSoup(r.text, "html.parser")
    print("   title:", soup.title.get_text(strip=True) if soup.title else None)
    tables = soup.find_all("table")
    print(f"   tables: {len(tables)}")
    for t in tables[:3]:
        heads = [th.get_text(" ", strip=True) for th in t.find_all("th")]
        rows = t.find_all("tr")
        print(f"   table headers: {heads}  rows: {len(rows)}")
        for tr in rows[1:3]:
            print("     row:", [td.get_text(' ', strip=True)[:60] for td in tr.find_all("td")], "| links:", [a.get("href") for a in tr.find_all("a")][:2])
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if re.search(r"next|prev|page|^\d+$|»|«|>", txt, re.I) or re.search(r"page=|pageno|p=", a["href"], re.I):
            print("   paging link:", txt, a["href"])
    return soup


def accept_disclaimer(c, r):
    """The site redirects everything to a Copyright & Disclaimer page; POSTing its form sets a cookie and returns to the page."""
    for _ in range(3):
        if "/Disclaimer" not in str(r.url) and "disclaimer.aspx" not in str(r.url).lower():
            return r
        soup = BeautifulSoup(r.text, "html.parser")
        form = next((f for f in soup.find_all("form") if "Disclaimer/Accept" in (f.get("action") or "")), None)
        if not form:
            print("   (disclaimer page but no Accept form found)"); return r
        data = {i.get("name"): i.get("value", "") for i in form.find_all("input") if i.get("name")}
        print(f"   accepting disclaimer: POST {form.get('action')} fields={list(data)} buttons={[b.get_text(strip=True) for b in form.find_all(['button','input'])][:4]}")
        r = c.post(BASE + form.get("action"), data=data)
        print(f"   -> {r.status_code} {r.url}")
    return r


def main():
    with httpx.Client(headers=H, follow_redirects=True, timeout=45, verify=False) as c:
        r = accept_disclaimer(c, c.get(BASE + "/Planning/WeeklyList/"))
        soup = show_page("weekly list index", r)
        if not soup:
            return
        links = []
        print("\n--- all links on the weekly list page ---")
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True)
            links.append((txt, a["href"]))
            print(f"   {txt[:60]!r:64} {a['href']}")
        forms = soup.find_all("form")
        print(f"\n   forms on page: {len(forms)}")
        for f in forms:
            print("   form:", f.get("method"), f.get("action"), [i.get("name") for i in f.find_all(["input", "select"])][:12])

        def first(pattern):
            for txt, href in links:
                if re.search(pattern, txt, re.I):
                    return href
        for label, pat in (("RECEIVED VALID (latest week with data: 2nd row)", r"received valid"), ("DETERMINED", r"determined")):
            matches = [h for t, h in links if re.search(pat, t, re.I)]
            print(f"\n{label}: {len(matches)} links; trying #2 then #1")
            for idx in (1, 0):
                if idx < len(matches):
                    url = matches[idx] if matches[idx].startswith("http") else BASE + (matches[idx] if matches[idx].startswith("/") else "/Planning/" + matches[idx])
                    rr = accept_disclaimer(c, c.get(url))
                    show_page(f"{label} link #{idx+1}", rr)
                    break

        for path in ("/Search", "/Search/Results", "/Search/Planning"):
            try:
                rr = accept_disclaimer(c, c.get(BASE + path))
                s2 = show_page(f"direct {path}", rr)
                if s2:
                    for f in s2.find_all("form"):
                        print("   FORM:", f.get("method"), f.get("action"))
                        for i in f.find_all(["input", "select", "textarea"]):
                            opts = [o.get_text(strip=True) for o in i.find_all("option")][:8] if i.name == "select" else ""
                            print("     ", i.name, i.get("type"), i.get("name"), (i.get("value") or "")[:40], opts)
            except Exception as e:
                print(f"direct {path}: {type(e).__name__}: {e}")


main()
