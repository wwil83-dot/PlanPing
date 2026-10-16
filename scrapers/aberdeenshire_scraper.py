#!/usr/bin/env python3
"""
PlanFind - Aberdeenshire Council weekly planning lists.

Reads the weekly lists Aberdeenshire Council publishes itself as PDFs
(https://publications.aberdeenshire.gov.uk/dataset/weekly-planning-lists), one PDF per area
per week. It does NOT use the Idox Public Access portal.

  python aberdeenshire_scraper.py                 # normal run (saves to Supabase)
  python aberdeenshire_scraper.py --test-file x.pdf   # parse one PDF locally, print, save nothing

Env: SUPABASE_URL, SUPABASE_KEY (required for a real run)
     DAYS_BACK=9      how far back to read lists (use 120 once for a backfill)
     MAX_PAGES=15     listing pages to follow
     DRY_RUN=1        do everything except write to the database
"""
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin, unquote

import pdfplumber
from bs4 import BeautifulSoup

import scot_common as sc

COUNCIL_NAME = "Aberdeenshire Council"
COVERAGE_SOURCE = "aberdeenshire_pdf"
SOURCE_LABEL = "aberdeenshire_weekly_list"
LISTING_URL = "https://publications.aberdeenshire.gov.uk/dataset/weekly-planning-lists"
DAYS_BACK = int(os.environ.get("DAYS_BACK", "9"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "15"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# ---------------------------------------------------------------------------- PDF parsing
# Layout confirmed from a real PDF (banff-buchan-12-october-2026.pdf): each application is
# a block starting "Ref Number | Site Address/Applicant Name | Proposal | Case Officer", four
# text columns at fixed x positions, then a bottom row "Ward | Community Council | Public
# Comment Expiry Date".
COL_B_X = 170
COL_C_X = 385
COL_D_X = 605
WARD_CC_X = 290
WARD_EXP_X = 520
PAGE_STRIDE = 10000.0

GRID_RE = re.compile(r"E\.\s*(\d{5,7})\s+N\.\s*(\d{5,7})")
REF_RE = re.compile(r"^[A-Z]{2,6}/\d{4}/\d+\S*$")
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b")


def _lines(words):
    """Group words into text lines (by y), left to right."""
    out = []
    for w in sorted(words, key=lambda w: (round(w["top"] / 3), w["x0"])):
        if out and abs(out[-1][0] - w["top"]) <= 3:
            out[-1][1].append(w)
        else:
            out.append([w["top"], [w]])
    return [" ".join(x["text"] for x in sorted(ws, key=lambda x: x["x0"])) for _, ws in out]


def _date(text: str) -> Optional[str]:
    text = " ".join(text.split())
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_ref(text: str) -> bool:
    return bool(re.match(r"^[A-Z]{2,}[/\-]\d", (text or "").strip()))


def _parse_block(body: list) -> dict:
    ward_hdr = [w for w in body if w["text"] == "Ward" and w["x0"] < 100]
    if ward_hdr:
        wy = ward_hdr[0]["top"]
        top_part = [w for w in body if w["top"] < wy - 1]
        ward_vals = [w for w in body if w["top"] > wy + 3]
    else:
        top_part, ward_vals = body, []

    col_a = [w for w in top_part if w["x0"] < COL_B_X]
    col_b = [w for w in top_part if COL_B_X <= w["x0"] < COL_C_X]
    col_c = [w for w in top_part if COL_C_X <= w["x0"] < COL_D_X]
    col_d = [w for w in top_part if w["x0"] >= COL_D_X]

    a_lines = _lines(col_a)
    ref = a_lines[0].strip() if a_lines else ""
    validated = None
    for j, ln in enumerate(a_lines):
        if ln.startswith("Date Validated") and j + 1 < len(a_lines):
            validated = _date(a_lines[j + 1])

    b_text = _clean(" ".join(_lines(col_b)))
    m = re.match(r"Site Address:\s*(.*?)\s*Applicant:\s*(.*)$", b_text)
    if m:
        address, applicant = m.group(1), m.group(2)
    else:
        address, applicant = re.sub(r"^Site Address:\s*", "", b_text), ""

    c_text = _clean(" ".join(_lines(col_c)))
    grid = GRID_RE.search(c_text)
    proposal = _clean(GRID_RE.sub("", c_text))

    ward = community = expiry = ""
    if ward_vals:
        ward = _clean(" ".join(_lines([w for w in ward_vals if w["x0"] < WARD_CC_X])))
        community = _clean(" ".join(_lines([w for w in ward_vals if WARD_CC_X <= w["x0"] < WARD_EXP_X])))
        expiry = _clean(" ".join(_lines([w for w in ward_vals if w["x0"] >= WARD_EXP_X])))

    pm = POSTCODE_RE.findall(address.upper())
    return {
        "reference": ref,
        "address": address,
        "postcode": pm[-1] if pm else None,
        "applicant": applicant,
        "description": proposal,
        "case_officer": _clean(" ".join(_lines(col_d))),
        "submitted_date": validated,
        "comment_expiry": _date(expiry) if expiry else None,
        "ward": ward,
        "community_council": community,
        "easting": int(grid.group(1)) if grid else None,
        "northing": int(grid.group(2)) if grid else None,
    }


def parse_pdf(path: str) -> tuple[list[dict], int, dict]:
    """Returns (applications, header_count, info).

    A table that runs over a page break repeats its 'Ref Number' header row on the next
    page, so the rest of an application can sit under a second header with no reference.
    Those continuation blocks are merged back into the application above them.
    info = {"pages", "continuations", "text_sample"} for diagnostics."""
    words, text_sample, pages = [], "", 0
    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if i == 0:
                text_sample = " ".join((page.extract_text() or "").split())[:300]
            for w in page.extract_words():
                w = dict(w)
                w["top"] = w["top"] + i * PAGE_STRIDE
                words.append(w)

    headers = []
    for w in words:
        if w["text"] == "Ref" and w["x0"] < 100:
            if any(abs(x["top"] - w["top"]) < 3 and x["text"] == "Number" for x in words):
                headers.append(w["top"])
    headers.sort()

    blocks, continuations = [], 0
    for idx, y0 in enumerate(headers):
        y1 = headers[idx + 1] if idx + 1 < len(headers) else float("inf")
        body = [w for w in words if y0 - 1 <= w["top"] < y1 and w["top"] - y0 < PAGE_STRIDE
                and w["top"] > y0 + 20]   # skip the two header rows
        first = _lines([w for w in body if w["x0"] < COL_B_X])
        first_line = first[0] if first else ""
        if blocks and not _looks_like_ref(first_line):
            blocks[-1].extend(body)        # continuation of the application above
            continuations += 1
        else:
            blocks.append(list(body))

    apps = [_parse_block(b) for b in blocks]
    return apps, len(headers), {"pages": pages, "continuations": continuations,
                                "text_sample": text_sample}


def application_type_from_proposal(proposal: str) -> str:
    """'Full Planning Permission for Change of Use...' -> 'Full Planning Permission'."""
    m = re.match(r"^(.{3,80}?)\s+for\s+", proposal or "", flags=re.I)
    return m.group(1).strip() if m else ""


def valid(app: dict) -> bool:
    return bool(REF_RE.match(app.get("reference") or "")) and bool(app.get("address"))


# ---------------------------------------------------------------------------- listing + run
LIST_NAME_RE = re.compile(r"^(?P<area>.+)-(?P<d>\d{1,2})-(?P<m>[a-z]+)-(?P<y>\d{4})\.pdf$", re.I)


def list_date_from_url(url: str):
    name = unquote(url.rsplit("/", 1)[-1])
    m = LIST_NAME_RE.match(name)
    if not m:
        return None, name
    try:
        d = datetime.strptime(f"{m['d']} {m['m']} {m['y']}", "%d %B %Y").date()
    except ValueError:
        return None, name
    return d, m["area"]


def collect_pdf_links(client, cutoff: date) -> list[tuple[str, date, str]]:
    """Walks the listing pages (newest first) and returns (url, list_date, area) in the window."""
    found, url, seen_urls = [], LISTING_URL, set()
    for page in range(MAX_PAGES):
        if not url or url in seen_urls:
            break
        seen_urls.add(url)
        html = sc.polite_get(client, url).text
        soup = BeautifulSoup(html, "html.parser")
        in_window = older = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/acblobstorage/" not in href or not href.lower().split("?")[0].endswith(".pdf"):
                continue
            full = urljoin(url, href)
            d, area = list_date_from_url(full)
            if d is None:
                sc.log(f"  ? could not read a date from {full}")
                continue
            if d >= cutoff:
                if all(full != f[0] for f in found):
                    found.append((full, d, area))
                in_window += 1
            else:
                older += 1
        sc.log(f"  listing page {page + 1}: {in_window} list(s) in window, {older} older")
        if older and not in_window:
            break
        nxt = next((a for a in soup.find_all("a", href=True)
                    if a.get_text(strip=True).lower() == "next"), None)
        url = urljoin(url, nxt["href"]) if nxt else None
    return found


def to_record(a: dict, portal_url: str) -> dict:
    ll = sc.grid_to_latlng(a.get("easting"), a.get("northing"))
    return {
        "reference": a["reference"],
        "address": a["address"],
        "postcode": a.get("postcode") or sc.extract_postcode(a["address"]),
        "lat": ll[0] if ll else None,
        "lng": ll[1] if ll else None,
        "description": a["description"],
        "application_type": application_type_from_proposal(a["description"]),
        "status": "pending",
        "submitted_date": a["submitted_date"],
        "decision_date": None,
        "council_url": portal_url,
    }


def parse_bytes(content: bytes):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        return parse_pdf(path)
    finally:
        os.unlink(path)


def main():
    if "--test-file" in sys.argv:
        path = sys.argv[sys.argv.index("--test-file") + 1]
        apps, headers, info = parse_pdf(path)
        sc.log(f"{len(apps)} application(s) parsed from {headers} header(s), "
               f"{info['continuations']} continuation(s) merged, {info['pages']} page(s)")
        for a in apps:
            sc.log(f"  {a['reference']} | {a['submitted_date']} | {a['address']} | "
                   f"{application_type_from_proposal(a['description'])} | grid {a['easting']},{a['northing']}")
        return

    sc.require_supabase()
    council = sc.find_council(COUNCIL_NAME)
    portal_url = council.get("portal_url") or LISTING_URL
    cutoff = date.today() - timedelta(days=DAYS_BACK)
    sc.log(f"[{datetime.now().isoformat(timespec='seconds')}] Aberdeenshire weekly lists - "
           f"council id {council['id']}, window from {cutoff} ({DAYS_BACK} days){' [DRY RUN]' if DRY_RUN else ''}")

    all_apps, problems = [], 0
    with sc.web_client() as client:
        try:
            links = collect_pdf_links(client, cutoff)
        except Exception as e:  # noqa: BLE001
            sc.log(f"x Could not read the listing page: {e}")
            sys.exit(1)
        sc.log(f"{len(links)} weekly PDF(s) in window")
        if not links:
            sc.log("! No lists found in the window - nothing published yet, or the page layout changed.")
        for url, d, area in links:
            try:
                content = sc.polite_get(client, url).content
                apps, headers, info = parse_bytes(content)
            except Exception as e:  # noqa: BLE001
                problems += 1
                sc.log(f"  x {area} {d}: {e}")
                continue
            good = [a for a in apps if valid(a)]
            bad = [a for a in apps if not valid(a)]
            flag = "" if not bad else "  <-- CHECK: some blocks were not read as applications"
            if flag:
                problems += 1
            extra = f", {info['continuations']} page-break merge(s)" if info["continuations"] else ""
            sc.log(f"  {area} {d}: {len(good)} application(s) from {headers} header(s){extra}{flag}")
            for a in bad[:3]:
                sc.log(f"      ? unread block: ref={a['reference']!r} address={a['address'][:60]!r} "
                       f"proposal={a['description'][:60]!r}")
            if headers == 0:
                sc.log(f"      ? no application blocks found; {info['pages']} page(s); "
                       f"page 1 text starts: {info['text_sample']!r}")
            all_apps.extend(to_record(a, portal_url) for a in good)

    sc.log(f"Total {len(all_apps)} application(s) from {len(links)} PDF(s); {problems} problem(s)")
    if DRY_RUN:
        for r in all_apps[:5]:
            sc.log(f"  e.g. {r['reference']} {r['submitted_date']} {r['address']}")
        return
    if not all_apps and problems:
        sc.log("Not touching the database: nothing parsed and there were problems.")
        sys.exit(1)
    sc.save_applications(council, all_apps, COVERAGE_SOURCE, SOURCE_LABEL, portal_url)


if __name__ == "__main__":
    main()
