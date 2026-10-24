#!/usr/bin/env python3
"""
PlanFind - Dorset Council scraper (custom ASP.NET WebForms register, plain HTTP).

Site: https://planning.dorsetcouncil.gov.uk  (councils.id 368; the old 'idox' label was wrong)
Flow (confirmed by dorset_probe / dorset_probe2):
  * /advsearch.aspx redirects to /disclaimer.aspx; POST with the 'Accept' button, then the form loads.
  * Date searches use the Telerik date pickers: set 'txtDateReceivedFrom' (yyyy-mm-dd), '...$dateInput'
    (dd/mm/yyyy) and the _dateInput_ClientState / _ClientState JSON, then press btnSearch3.
    Received window -> txtDateReceivedFrom/To; decision window -> txtDateIssuedFrom/To.
  * Results: /searchresults.aspx, 10 per page, 'Page x of N'; each item has reference, Location, Proposal,
    Decision, Decision Date and a link plandisp.aspx?recno=N. Next page = POST the results form with the
    RadDataPager1 Next button.
  * Detail page (plandisp.aspx): Status, Type, Valid Date, Issue Date, Address.
Two sweeps (received window with a lag, and decision/issued window) merged by reference; decided wins.

Env: SUPABASE_URL, SUPABASE_KEY (not needed for DRY_RUN), DORSET_COUNCIL_ID (368), WINDOW_DAYS (14; 60 for a
backfill), VALIDATION_LAG_DAYS (21), SLICE_DAYS (7), DETAIL_DELAY_SECONDS (0.7), MAX_MINUTES (45), DRY_RUN.
"""
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

import northdevon_scraper as N  # tested helpers: _dmy, _extract_postcode, _upsert, _patch_council, geocode, _h

BASE = "https://planning.dorsetcouncil.gov.uk"
SOURCE = "dorset_scraper"
CP = "ctl00$ContentPlaceHolder1$"
CPI = "ctl00_ContentPlaceHolder1_"
NEXT_BTN = CP + "lvResults$RadDataPager1$ctl02$NextButton"

SUPABASE_URL, SUPABASE_KEY = N.SUPABASE_URL, N.SUPABASE_KEY
WINDOW_DAYS = N.WINDOW_DAYS
LAG_DAYS = int(os.environ.get("VALIDATION_LAG_DAYS", "21"))
SLICE_DAYS = int(os.environ.get("SLICE_DAYS", "7"))
DETAIL_DELAY = float(os.environ.get("DETAIL_DELAY_SECONDS", "0.7"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "45"))
DRY_RUN = N.DRY_RUN
UA = N.UA
START = N.START


def minutes() -> float:
    return (time.monotonic() - START) / 60


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline)
# ---------------------------------------------------------------------------
def form_payload(html: str) -> tuple:
    """The page's main form -> (action, dict of the values a browser would post, without any button)."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="aspnetForm") or soup.find("form")
    data = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name:
            continue
        if el.name == "input":
            t = (el.get("type") or "text").lower()
            if t in ("submit", "button", "image", "reset", "file"):
                continue
            if t in ("checkbox", "radio") and not el.has_attr("checked"):
                continue
            data[name] = el.get("value", "on" if t in ("checkbox", "radio") else "")
        elif el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            data[name] = opt.get("value", opt.get_text(strip=True)) if opt else ""
        else:
            data[name] = el.get_text()
    return form.get("action", ""), data


def picker_fields(prefix: str, d: date) -> dict:
    """Telerik RadDatePicker values the way the browser posts them (variant 2 of dorset_probe2)."""
    s = d.strftime("%Y-%m-%d-00-00-00")
    return {
        CP + prefix: d.strftime("%Y-%m-%d"),
        CP + prefix + "$dateInput": d.strftime("%d/%m/%Y"),
        CPI + prefix + "_dateInput_ClientState": json.dumps(
            {"enabled": True, "emptyMessage": "", "validationText": s, "valueAsString": s,
             "minDateStr": "1980-01-01-00-00-00", "maxDateStr": "2099-12-31-00-00-00",
             "lastSetTextBoxValue": d.strftime("%d/%m/%Y")}, separators=(",", ":")),
        CPI + prefix + "_ClientState": json.dumps(
            {"minDateStr": "1980-01-01-00-00-00", "maxDateStr": "2099-12-30-00-00-00"}, separators=(",", ":")),
    }


def parse_results(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for box in soup.select("div.emphasise-area"):
        a = box.find("h2")
        link = a.find("a") if a else None
        if not link or not link.get("href"):
            continue
        fields = {}
        for h3 in box.find_all("h3"):
            label = h3.get_text(" ", strip=True).rstrip(":").lower()
            parts = []
            for sib in h3.next_siblings:
                if getattr(sib, "name", None) == "h3" or (hasattr(sib, "find") and getattr(sib, "name", None) and sib.find("h3")):
                    break
                parts.append(sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip())
            fields[label] = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
        out.append({"reference": link.get_text(strip=True), "href": link["href"],
                    "location": fields.get("location", ""), "proposal": fields.get("proposal", ""),
                    "decision": fields.get("decision", ""), "decision_date_text": fields.get("decision date", "")})
    return out


def page_counts(html: str) -> Optional[tuple]:
    m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", BeautifulSoup(html, "html.parser").get_text(" "))
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_detail(text: str) -> dict:
    t = re.sub(r"\s+", " ", text)
    g = lambda pat: (re.search(pat, t).group(1).strip() if re.search(pat, t) else "")
    addr = re.search(r"\sAddress\s+((?:(?!\bAddress\b).)+?)\s+Easting", t)
    return {
        "status": g(r"\bStatus\s+(.+?)\s+Type\s"),
        "type": g(r"\sType\s+(.+?)\s+Committee / Delegated"),
        "valid": N._dmy(g(r"Valid Date\s+(\d{2}/\d{2}/\d{4})")),
        "issued": N._dmy(g(r"Issue Date\s+(\d{2}/\d{2}/\d{4})")),
        "address": addr.group(1).strip() if addr else "",
    }


_UNMAPPED: set = set()


def map_status(detail_status: str, decision: str, has_decision: bool) -> str:
    texts = [(decision or "").lower(), (detail_status or "").lower()]
    joined = " | ".join(texts)
    if "withdraw" in joined:
        return "withdrawn"
    d = texts[0]
    if d:
        if "split" in d:
            return "approved"
        if "refus" in d or "reject" in d or "dismiss" in d:
            return "refused"
        if any(x in d for x in ("grant", "approv", "permit", "consent", "no objection", "not required", "certif",
                                "lawful", "no further action", "not expedient")) and "not permit" not in d:
            return "approved"
        if any(x in d for x in ("invalid", "returned", "closed", "cancel", "not determined", "disposed", "lapsed")):
            return "closed"
    s = texts[1]
    if any(x in s for x in ("invalid", "returned", "closed", "cancel", "not proceed", "lapsed", "disposed")):
        return "closed"
    if has_decision:
        key = f"{detail_status} | {decision}"
        if key not in _UNMAPPED:
            _UNMAPPED.add(key)
            print(f"    ⚠ UNMAPPED STATUS {key!r}: saved as 'pending' until a mapping is added")
    return "pending"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
async def get_form_page(client: httpx.AsyncClient, path: str = "/advsearch.aspx") -> httpx.Response:
    r = await N._req(client, "GET", BASE + path)
    if "disclaimer.aspx" in str(r.url).lower():
        action, data = form_payload(r.text)
        data[CP + "btnAccept"] = "Accept"
        r = await N._req(client, "POST", urljoin(str(r.url), action), data=data, headers={"Referer": str(r.url)})
        r = await N._req(client, "GET", BASE + path)
    return r


async def search_window(client: httpx.AsyncClient, kind: str, a: date, b: date) -> list:
    pf, pt = (("txtDateReceivedFrom", "txtDateReceivedTo") if kind == "received"
              else ("txtDateIssuedFrom", "txtDateIssuedTo"))
    page = await get_form_page(client)
    action, data = form_payload(page.text)
    if "txtDateReceivedFrom" not in page.text:
        raise RuntimeError("advanced search form not reached (disclaimer not accepted?)")
    data.update(picker_fields(pf, a))
    data.update(picker_fields(pt, b))
    data[CP + "btnSearch3"] = "Search"
    r = await N._req(client, "POST", urljoin(str(page.url), action), data=data, headers={"Referer": str(page.url)})
    pc = page_counts(r.text)
    rows = parse_results(r.text)
    if not pc:
        return rows  # zero results or a single page without a pager
    cur, total = pc
    if total > 1000:
        raise RuntimeError(f"date filter not applied: {total} pages")
    while cur < total:
        action, data = form_payload(r.text)
        data[NEXT_BTN] = " "
        prev = [x["reference"] for x in rows[-10:]]
        r = await N._req(client, "POST", urljoin(str(r.url), action), data=data, headers={"Referer": str(r.url)})
        new = parse_results(r.text)
        if not new or [x["reference"] for x in new] == prev:
            print(f"    ⚠ paging did not advance after page {cur} of {total} ({kind} {a:%d/%m}-{b:%d/%m})")
            break
        rows += new
        cur += 1
        await asyncio.sleep(0.5)
    return rows


async def sweep(client, kind: str, d_from: date, d_to: date) -> dict:
    out: dict = {}
    start = d_from
    while start <= d_to:
        if minutes() > MAX_MINUTES:
            print(f"    ⚠ MAX_MINUTES reached in the {kind} sweep at {start:%d/%m/%Y}")
            break
        end = min(start + timedelta(days=SLICE_DAYS - 1), d_to)
        rows = await search_window(client, kind, start, end)
        for row in rows:
            out[row["reference"]] = row
        print(f"    ℹ {kind} {start:%d/%m/%Y}-{end:%d/%m/%Y}: {len(rows)} rows, {len(out)} total")
        start = end + timedelta(days=1)
        await asyncio.sleep(1)
    return out


async def council_id() -> int:
    env = os.environ.get("DORSET_COUNCIL_ID", "").strip()
    if env.isdigit():
        return int(env)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/councils",
                        params={"select": "id,name,system,coverage_source", "name": "ilike.*Dorset*"}, headers=N._h())
        r.raise_for_status()
        rows = r.json()
    if len(rows) != 1:
        raise RuntimeError(f"Expected one Dorset councils row, found {len(rows)}: {rows}. Set DORSET_COUNCIL_ID.")
    print(f"    ℹ councils row: {rows[0]}")
    return rows[0]["id"]


async def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] PlanFind Dorset scraper ({BASE})")
    print(f"Window: {WINDOW_DAYS} days (+{LAG_DAYS} lag on received)   slice {SLICE_DAYS}d   DRY_RUN: {DRY_RUN}   "
          f"detail delay {DETAIL_DELAY}s   max {MAX_MINUTES} min")
    print(f"SUPABASE: {'set' if SUPABASE_URL else 'NOT SET'}\n")
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_KEY):
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    cid = await council_id() if (SUPABASE_URL and SUPABASE_KEY) else None
    today = date.today()
    d_from = today - timedelta(days=WINDOW_DAYS)

    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"}) as client:
        received = await sweep(client, "received", d_from - timedelta(days=LAG_DAYS), today)
        decided = await sweep(client, "decided", d_from, today)
        refs = list(dict.fromkeys(list(decided) + list(received)))
        print(f"    ℹ received list {len(received)}, decided list {len(decided)}, unique {len(refs)}")

        apps, failed, timed_out = [], 0, False
        for n, ref in enumerate(refs, 1):
            if minutes() > MAX_MINUTES:
                timed_out = True
                print(f"    ⚠ MAX_MINUTES reached after {n - 1} of {len(refs)} detail pages; saving what we have")
                break
            row = decided.get(ref) or received.get(ref)
            url = urljoin(BASE + "/", row["href"])
            try:
                r = await N._req(client, "GET", url)
                det = parse_detail(BeautifulSoup(r.text, "html.parser").get_text(" "))
            except Exception as e:
                failed += 1
                print(f"    ⚠ detail {ref} failed: {e}")
                continue
            dec_date = N._dmy(row["decision_date_text"]) or det["issued"]
            status = map_status(det["status"], row["decision"], bool(dec_date or row["decision"]))
            address = det["address"] or re.sub(r"\s+", " ", row["location"])
            apps.append({
                "reference": ref, "address": address, "postcode": N._extract_postcode(address),
                "description": row["proposal"] or None, "application_type": det["type"] or None, "status": status,
                "submitted_date": det["valid"].isoformat() if det["valid"] else None,
                "decision_date": dec_date.isoformat() if (dec_date and status != "pending") else None,
                "council_url": url, "_combo": (det["status"], row["decision"]),
            })
            if n % 25 == 0:
                print(f"    ℹ detail pages {n}/{len(refs)}")
            await asyncio.sleep(DETAIL_DELAY)

    print(f"    ℹ parsed {len(apps)}; detail failures {failed}")
    print(f"    ℹ by status: {dict(Counter(a['status'] for a in apps))}")
    print("    ℹ detail status | list decision -> status (count):")
    for (combo, st), n in Counter((a["_combo"], a["status"]) for a in apps).most_common(40):
        print(f"        {combo[0]!r:<32} | {combo[1]!r:<40} -> {st:<9} x{n}")
    print(f"    ℹ application types: {dict(Counter(a['application_type'] for a in apps).most_common(14))}")
    print(f"    ℹ missing proposal: {sum(1 for a in apps if not a['description'])}; "
          f"missing postcode: {sum(1 for a in apps if not a['postcode'])}; "
          f"missing submitted date: {sum(1 for a in apps if not a['submitted_date'])}")
    if DRY_RUN:
        print("\n    --- 6 sample rows ---")
        for a in apps[:6]:
            print("   ", {k: v for k, v in a.items() if not k.startswith("_")})
        print("\nDRY_RUN: nothing written.")
        return
    if not apps:
        print("    ✗ nothing found - not saving")
        await N._patch_council(cid, {"last_scraped_at": datetime.now(timezone.utc).isoformat()})
        sys.exit(1)

    coords = await N.geocode([a["postcode"] for a in apps if a.get("postcode")])
    for a in apps:
        pc = (a.get("postcode") or "").strip().upper().replace(" ", "")
        a["lat"], a["lng"] = coords.get(pc, (None, None))
    geocoded = [(a["lat"], a["lng"]) for a in apps if a.get("lat")]
    print(f"    Geocoded {len(geocoded)} of {len(apps)} applications")
    if geocoded:
        clat, clng = statistics.median(g[0] for g in geocoded), statistics.median(g[1] for g in geocoded)
        k = 0
        for a in apps:
            if not a.get("lat"):
                a["lat"], a["lng"] = clat, clng
                k += 1
        if k:
            print(f"    Council centroid fallback for {k} apps")
    records = [{"council_id": cid, "reference": a["reference"], "address": a["address"], "postcode": a["postcode"],
                "lat": a.get("lat"), "lng": a.get("lng"), "description": a["description"],
                "application_type": a["application_type"], "status": a["status"],
                "submitted_date": a["submitted_date"], "decision_date": a["decision_date"],
                "council_url": a["council_url"], "source": SOURCE} for a in apps]
    saved, ok = 0, True
    for i in range(0, len(records), 50):
        if await N._upsert(records[i:i + 50]):
            saved += len(records[i:i + 50])
        else:
            ok = False
    now = datetime.now(timezone.utc).isoformat()
    if ok and not timed_out:
        await N._patch_council(cid, {"coverage_source": SOURCE, "last_scraped_at": now, "last_saved_at": now,
                                     "consecutive_empty_runs": 0, "active": True})
        print(f"    ✓ Saved {saved}")
    else:
        print(f"    ⚠ Partial save: {saved} of {len(records)}")
        if saved:
            await N._patch_council(cid, {"last_saved_at": now, "consecutive_empty_runs": 0})
    print("\n" + "=" * 50)
    print(f"Finished in {minutes():.1f} minutes")
    print(f"Applications saved: {saved}")
    if not ok or timed_out:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
