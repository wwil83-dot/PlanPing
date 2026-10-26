"""Kensington & Chelsea recon, round 9 (council id 45). Writes nothing.

Round 8 results (Atlas search app, client-Dj7XTNb0.js):
  * Ff (API base) and Zu (site base) are imported from ./Error404-BfHirmgl.js (minified alias z / u).
  * data calls: `${Ff}/cases/${Fe}` (binary, decoded with nl.decode(...).results), `${Ff}/searchMapPoints/${Fe}`
    (binary), `${Ff}/caseCounts/${Fe}` (JSON {Applications, Decisions, Appeals});
    Fe = `${yk(userSearchItem)}` + `?` + params + `&sort=N`; for 'everywhere' yk returns the constant `ea`.
  * params: caseStatus=<bitmask Awaiting1 Granted2 Refused4 SplitDecision8 Withdrawn16>, caseType=<Application1 Appeal2
    Decision4>, planningType=<bitmask ACV1 ARB2 CA4 CC8 CL16 CON32 E64 EIA128 GS256 LB512 NMA1024 NOT2048 OB4096 PA8192
    PC16384 PP32768 XR65536>, dateFrom/dateTo = epoch ms, dateWeeks/dateMonths = epoch-ms lists, keywords = btoa(list
    joined by '---'); sort 1 Latest, 2 Oldest, 4 AddressThenStatus.

This round:
  1. prints the whole Error404 chunk (where Ff / Zu are defined)
  2. prints the definition of `ea` and of the binary codec (nl / tl / Nr) from the main bundle
  3. calls caseCounts and cases for the last 7 days against every plausible API base found, with ScraperAPI,
     and prints status, first bytes (hex) and the readable strings inside the cases response
About 10-15 requests (100-150 credits).
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

KEY = os.environ.get("SCRAPERAPI_KEY", "")
SITE = "https://www.rbkc.gov.uk"
CLIENT = f"{SITE}/planningsearch/_build/assets/client-Dj7XTNb0.js"
CHUNK = f"{SITE}/planningsearch/_build/assets/Error404-BfHirmgl.js"
API = "https://api.scraperapi.com/"
LON = ZoneInfo("Europe/London")


def get(url, binary=False, extra=None, timeout=150):
    params = {"api_key": KEY, "url": url, "country_code": "gb"}
    if binary:
        params["binary_target"] = "true"
    if extra:
        params.update(extra)
    t0 = time.time()
    try:
        r = httpx.get(API, params=params, timeout=timeout, follow_redirects=True)
    except Exception as e:
        print(f"  ERROR {url}: {str(e)[:150]}")
        return None
    print(f"  GET {url[:160]} -> HTTP {r.status_code} {len(r.content)}B ({time.time() - t0:.0f}s)")
    return r


def span(js, i, before, after):
    return js[max(0, i - before): i + after].replace("\n", " ")


def ident(name):
    return r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])"


def midnight_ms(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=LON).timestamp() * 1000)


def readable_strings(b, minlen=5, limit=60):
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, b):
        out.append(m.group(0).decode("ascii"))
        if len(out) >= limit:
            break
    return out


def hexhead(b, n=240):
    return " ".join(f"{x:02x}" for x in b[:n])


def main():
    if not KEY:
        print("SCRAPERAPI_KEY not set")
        return 1

    print("\n== 1. Error404 chunk (Ff / Zu live here)")
    r = get(CHUNK)
    chunk = r.text if r is not None and r.status_code == 200 else ""
    print(chunk[:6500])
    cands = []
    for m in re.finditer(r"""(["'`])(https?://[^"'`\s]{6,200}|/[A-Za-z0-9_\-./]{3,120})\1""", chunk):
        s = m.group(2)
        if re.search(r"rbkc|api|atlas|planningsearch", s, re.I) and not re.search(r"\.(css|png|svg|js)$", s):
            cands.append(s)
    cands = list(dict.fromkeys(cands))
    print(f"\n  candidate base strings in chunk: {cands}")

    print("\n== 2. main bundle: `ea`, codec nl / tl / Nr")
    r2 = get(CLIENT)
    js = r2.text if r2 is not None and r2.status_code == 200 else ""
    ea = None
    for m in re.finditer(ident("ea") + r"\s*=\s*(?!=)", js):
        print(f"  ea def: ...{span(js, m.start(), 40, 220)}...")
        mm = re.match(r"""\s*=\s*(["'`])([^"'`]+)\1""", js[m.start() + 2:m.start() + 80]) if False else None
        lit = re.search(r"""ea\s*=\s*(["'`])([^"'`]{1,80})\1""", js[m.start():m.start() + 120])
        if lit and not ea:
            ea = lit.group(2)
    print(f"  ea literal: {ea!r}")
    for name in ("nl", "tl", "Nr"):
        for m in list(re.finditer(ident(name) + r"\s*=\s*(?!=)", js))[:1]:
            print(f"\n  --- {name} definition ---")
            print("  " + span(js, m.start(), 20, 4200 if name == "nl" else 2800))
    for m in list(re.finditer(r"class\s+Nr\b", js))[:1]:
        print("\n  --- class Nr ---")
        print("  " + span(js, m.start(), 0, 3500))

    print("\n== 3. live calls")
    today = date.today()
    frm = midnight_ms(today - timedelta(days=7))
    to = midnight_ms(today)
    qid_candidates = [ea] if ea else []
    qid_candidates += [q for q in ("everywhere", "EVERYWHERE", "all") if q not in qid_candidates]
    bases = []
    for c in cands:
        b = c if c.startswith("http") else SITE + c
        b = b.rstrip("/")
        if b not in bases:
            bases.append(b)
    for guess in (f"{SITE}/planningsearch/api", f"{SITE}/planningsearch/_api", "https://atlas.rbkc.gov.uk/api",
                  f"{SITE}/api"):
        if guess not in bases:
            bases.append(guess)
    print(f"  bases to try: {bases}")
    print(f"  qids to try: {qid_candidates}")
    shown = 0
    done = False
    for base in bases[:8]:
        for qid in qid_candidates[:3]:
            url = f"{base}/caseCounts/{qid}?dateFrom={frm}&dateTo={to}&sort=1"
            rr = get(url)
            if rr is None:
                continue
            body = rr.content[:300]
            print(f"    status {rr.status_code}; first bytes: {body[:200]!r}")
            if rr.status_code == 200:
                try:
                    print(f"    JSON: {json.dumps(rr.json())[:300]}")
                except Exception:
                    pass
                ru = f"{base}/cases/{qid}?dateFrom={frm}&dateTo={to}&sort=1"
                for binary in (False, True):
                    rc = get(ru, binary=binary)
                    if rc is None or rc.status_code != 200:
                        continue
                    b = rc.content
                    print(f"    cases ({'binary_target' if binary else 'plain'}): {len(b)} bytes, content-type={rc.headers.get('content-type')}")
                    print(f"    hex head: {hexhead(b)}")
                    ss = readable_strings(b)
                    print(f"    {len(ss)}+ readable strings, first 60:")
                    for s in ss:
                        print(f"        {s[:140]!r}")
                    import base64
                    print(f"    base64 of first 3000 bytes: {base64.b64encode(b[:3000]).decode()}")
                    open(f"/tmp/kc_cases_{'bin' if binary else 'plain'}.bin", "wb").write(b)
                    shown += 1
                done = True
                break
        if done:
            break
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
