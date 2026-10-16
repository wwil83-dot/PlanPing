#!/usr/bin/env python3
"""
PlanFind - East Lothian Council weekly lists of registered applications.

Reads the weekly "Registered Applications" documents East Lothian Council publishes itself
(RTF / Word) on its planning pages. It does NOT use the Idox Public Access portal.

  python east_lothian_scraper.py                       # normal run (saves to Supabase)
  python east_lothian_scraper.py --test-file x.rtf     # parse one file locally, print, save nothing

Env: SUPABASE_URL, SUPABASE_KEY (required for a real run)
     DAYS_BACK=9      how far back to read lists (use 120 once for a backfill)
     DRY_RUN=1        do everything except write to the database
"""
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

import scot_common as sc

COUNCIL_NAME = "East Lothian Council"
COVERAGE_SOURCE = "east_lothian_lists"
SOURCE_LABEL = "east_lothian_weekly_list"
BASE = ("https://www.eastlothian.gov.uk/planning-environment-and-building-standards/"
        "apply-pay-view-and-comment-planning-applications/")
DAYS_BACK = int(os.environ.get("DAYS_BACK", "9"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

# Only suffixes we are sure about get a readable name; anything else keeps its raw code.
TYPE_NAMES = {
    "P": "Planning Permission",
    "PPP": "Planning Permission in Principle",
    "LBC": "Listed Building Consent",
    "ADV": "Advertisement Consent",
    "AMM": "Approval of Matters Specified in Conditions",
}

FILE_RE = re.compile(r"(\d{8})\s+Registered\s+Applications\.(rtf|docx|doc)$", re.I)


# ------------------------------------------------------------------------ text extraction
def rtf_to_text(raw: bytes) -> str:
    from striprtf.striprtf import rtf_to_text as _r
    return _r(raw.decode("cp1252", errors="replace"))


def docx_to_text(path: str) -> str:
    """Flattens a Word file into the same 'Label|Value|...' lines the RTF gives.
    Tables become one line per row (cells joined by '|'); paragraphs stay as lines."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(path)
    lines = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, d).text)
        elif tag == "tbl":
            for row in Table(child, d).rows:
                cells, seen = [], set()
                for c in row.cells:
                    if id(c._tc) in seen:
                        continue
                    seen.add(id(c._tc))
                    cells.append(c.text.strip())
                lines.append("|".join(cells) + "|")
    return "\n".join(lines)


def doc_to_text(path: str) -> Optional[str]:
    """Old .doc via LibreOffice if it is installed; otherwise None."""
    outdir = tempfile.mkdtemp()
    try:
        subprocess.run(["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", outdir, path],
                       check=True, capture_output=True, timeout=120)
        out = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".txt")
        return open(out, encoding="utf-8", errors="replace").read()
    except Exception:  # noqa: BLE001
        return None


def file_to_text(name: str, content: bytes) -> Optional[str]:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "rtf":
        return rtf_to_text(content)
    with tempfile.NamedTemporaryFile(suffix="." + ext, delete=False) as f:
        f.write(content)
        path = f.name
    try:
        if ext == "docx":
            return docx_to_text(path)
        if ext == "doc":
            return doc_to_text(path)
    finally:
        os.unlink(path)
    return None


# ------------------------------------------------------------------------ record parsing
def _d(text: str) -> Optional[str]:
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", " ".join((text or "").split()), flags=re.I)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _field(chunk: str, label: str, next_label: str) -> str:
    m = re.search(r"(?:^|\n)\s*" + label + r"\s*\|(.*?)\|?\s*\n\s*" + next_label + r"\s*\|", chunk, re.S)
    return " ".join(m.group(1).split()).strip("| ") if m else ""


def parse_records(text: str) -> tuple[list[dict], Optional[int]]:
    """Returns (applications, footer_count). footer_count is the 'N Applications Registered'
    figure at the foot of the list, used to check nothing was missed."""
    text = text.replace("\r", "")
    footer = re.search(r"(\d+)\s+Applications?\s+Registered", text)
    chunks = re.split(r"(?m)^\s*App No\s*\|", text)[1:]
    apps = []
    for ch in chunks:
        ref = ch.split("|", 1)[0].strip()
        if not re.match(r"^\d{2}/\d{3,6}/[A-Z]+$", ref):
            continue
        ch = "\n" + ch
        proposal = _field(ch, "Proposal", "Location")
        location = _field(ch, "Location", "Case Officer")
        registered = re.search(r"\n\s*Date Registered\s*\|(.*?)\|", ch, re.S)
        suffix = ref.rsplit("/", 1)[-1].upper()
        apps.append({
            "reference": ref,
            "description": proposal,
            "address": location,
            "postcode": sc.extract_postcode(location),
            "application_type": TYPE_NAMES.get(suffix, suffix),
            "submitted_date": _d(registered.group(1)) if registered else None,
            "status": "pending",
        })
    return apps, int(footer.group(1)) if footer else None


def valid(a: dict) -> bool:
    return bool(a["reference"]) and bool(a["address"]) and bool(a["submitted_date"])


# ------------------------------------------------------------------------ listing pages
def month_pages(client, cutoff: date, today: date) -> list[str]:
    """URLs of the monthly 'weekly lists' pages covering the window."""
    months, y, m = [], today.year, today.month
    while date(y, m, 1) >= date(cutoff.year, cutoff.month, 1):
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    urls = []
    for y, m in months:
        name = MONTHS[m - 1]
        found = None
        try:
            index = BASE + f"planning-applications-{y}"
            soup = BeautifulSoup(sc.polite_get(client, index).text, "html.parser")
            for a in soup.find_all("a", href=True):
                t = a.get_text(" ", strip=True).lower()
                if name in t and str(y) in t and "weekly" in (t + a["href"].lower()):
                    found = urljoin(index, a["href"])
                    break
        except Exception as e:  # noqa: BLE001
            sc.log(f"  ! index page for {y} not readable ({e}); trying a guessed address")
        urls.append(found or BASE + f"planning-applications-{y}/{name}-{y}-weekly-lists")
    return urls


def registered_links(client, page_url: str, cutoff: date) -> list[tuple[str, str, date]]:
    soup = BeautifulSoup(sc.polite_get(client, page_url).text, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        name = unquote(href.rsplit("/", 1)[-1])
        m = FILE_RE.search(name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%d%m%Y").date()
        except ValueError:
            continue
        if d >= cutoff:
            out.append((urljoin(page_url, href), name, d))
    return out


def main():
    if "--test-file" in sys.argv:
        path = sys.argv[sys.argv.index("--test-file") + 1]
        content = open(path, "rb").read()
        text = file_to_text(os.path.basename(path), content)
        if text is None:
            sc.log("Could not convert this file type here.")
            return
        apps, footer = parse_records(text)
        sc.log(f"{len(apps)} application(s) parsed; footer says {footer}")
        for a in apps:
            sc.log(f"  {a['reference']} | {a['submitted_date']} | {a['application_type']} | "
                   f"{a['address']} | {a['postcode']}")
        return

    sc.require_supabase()
    council = sc.find_council(COUNCIL_NAME)
    portal_url = council.get("portal_url") or (BASE + "planning-applications-" + str(date.today().year))
    today = date.today()
    cutoff = today - timedelta(days=DAYS_BACK)
    sc.log(f"[{datetime.now().isoformat(timespec='seconds')}] East Lothian weekly lists - "
           f"council id {council['id']}, window from {cutoff} ({DAYS_BACK} days){' [DRY RUN]' if DRY_RUN else ''}")

    all_apps, problems, files = [], 0, 0
    with sc.web_client() as client:
        links = []
        for page in month_pages(client, cutoff, today):
            try:
                found = registered_links(client, page, cutoff)
            except Exception as e:  # noqa: BLE001
                problems += 1
                sc.log(f"  x could not read {page}: {e}")
                continue
            sc.log(f"  {page.rsplit('/', 1)[-1]}: {len(found)} registered list(s) in window")
            links.extend(found)
        if not links:
            sc.log("! No lists found in the window - nothing published yet, or the page layout changed.")
        for url, name, d in sorted(set(links), key=lambda x: x[2]):
            files += 1
            try:
                content = sc.polite_get(client, url).content
                text = file_to_text(name, content)
                if text is None:
                    problems += 1
                    sc.log(f"  x {name}: cannot convert this file type here (needs LibreOffice for .doc)")
                    continue
                apps, footer = parse_records(text)
            except Exception as e:  # noqa: BLE001
                problems += 1
                sc.log(f"  x {name}: {e}")
                continue
            good = [a for a in apps if valid(a)]
            flag = ""
            if footer is not None and footer != len(good):
                problems += 1
                flag = f"  <-- MISMATCH: footer says {footer}"
            sc.log(f"  {name}: {len(good)} application(s){flag}")
            for a in good:
                a["council_url"] = portal_url
            all_apps.extend(good)

    sc.log(f"Total {len(all_apps)} application(s) from {files} file(s); {problems} problem(s)")
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
