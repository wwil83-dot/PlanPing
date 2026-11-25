#!/usr/bin/env python3
"""
PlanFind alert dispatcher - called by GitHub Actions (job `alerts`, every morning after the overnight scrapes).
Uses the Supabase REST API only (no asyncpg/TCP) to bypass GH Actions IP blocks.

Changes 2026-11-25 (from the original):
  - an alert is only logged as sent when the email was really accepted by Resend (before, a failed send was still logged,
    so those applications were never alerted again); any failed send makes the run fail so it shows red
  - addresses and descriptions are HTML-escaped; links point to the PlanFind application page as well as the council's
  - uses the plain-English AI summary when there is one, and shows the application's status
  - List-Unsubscribe headers (better inbox placement); redesigned layout
  - looks back 14 days instead of 7 (applications added late by a scraper backfill still arrive); the alert_log means
    nothing is ever sent twice
"""
import asyncio
import html as _html
import os
import httpx
from datetime import date

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "alerts@planfind.co.uk")
BASE_URL = (os.environ.get("BASE_URL", "https://www.planfind.co.uk") or "https://www.planfind.co.uk").rstrip("/")
DAYS_BACK = int(os.environ.get("ALERT_DAYS_BACK", "14"))
MAX_IN_EMAIL = 10

if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("SUPABASE_URL and SUPABASE_KEY must be set")

_API = f"{SUPABASE_URL}/rest/v1"
_H = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
_SELECT = "id,reference,address,description,application_type,status,submitted_date,council_url"


async def _get(table: str, **params) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{_API}/{table}", params=params, headers=_H)
        r.raise_for_status()
        return r.json()


async def _rpc(func: str, body: dict) -> list:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_API}/rpc/{func}", json=body, headers=_H)
        r.raise_for_status()
        return r.json()


async def _insert(table: str, rows: list, upsert: bool = False) -> int:
    prefer = "resolution=merge-duplicates" if upsert else "return=minimal"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{_API}/{table}", json=rows, headers={**_H, "Prefer": prefer})
        return r.status_code


async def _send_email(to: str, subject: str, body_html: str, unsub_url: str) -> bool:
    """True only when the email was accepted (or in dry-run mode, where nothing is logged as sent either)."""
    if not RESEND_API_KEY:
        print(f"    [DRY RUN - no RESEND_API_KEY] Would email {to}: {subject}")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": FROM_EMAIL, "to": to, "subject": subject, "html": body_html,
                      "headers": {"List-Unsubscribe": f"<{unsub_url}>",
                                      "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}},
            )
    except Exception as e:
        print(f"    Email error: {type(e).__name__}: {e}")
        return False
    if r.status_code not in (200, 201):
        print(f"    Email error {r.status_code}: {r.text[:200]}")
        return False
    return True


def _days_ago(iso_date) -> str:
    if not iso_date:
        return "Date not shown"
    try:
        d = date.fromisoformat(str(iso_date)[:10])
        delta = (date.today() - d).days
        if delta <= 0:
            return "Submitted today"
        if delta == 1:
            return "Submitted yesterday"
        if delta < 7:
            return f"Submitted {delta} days ago"
        return "Submitted " + d.strftime("%-d %b %Y")
    except Exception:
        return str(iso_date)[:10]


_STATUS = {  # label, text colour, background
    "pending": ("Awaiting decision", "#92400e", "#fef3c7"),
    "approved": ("Approved", "#166534", "#dcfce7"),
    "refused": ("Refused", "#991b1b", "#fee2e2"),
    "withdrawn": ("Withdrawn", "#475569", "#e2e8f0"),
    "closed": ("Decided", "#475569", "#e2e8f0"),
}


def _esc(v) -> str:
    return _html.escape(str(v or ""), quote=True)


def _build_email(sub: dict, apps: list) -> tuple:
    count = len(apps)
    plural = "s" if count != 1 else ""
    postcode = _esc(sub.get("postcode") or "your area")
    unsub_url = f"{BASE_URL}/unsubscribe/{sub['unsubscribe_token']}"
    search_url = f"{BASE_URL}/search?postcode={_esc(sub.get('postcode') or '')}"

    cards = ""
    for a in apps[:MAX_IN_EMAIL]:
        label, fg, bg = _STATUS.get((a.get("status") or "pending").lower(), _STATUS["pending"])
        text = a.get("ai_summary") or a.get("description") or ""
        text = text[:170] + ("..." if len(text) > 170 else "")
        links = f'<a href="{BASE_URL}/application/{a["id"]}" style="color:#1a5fa8;font-weight:600;text-decoration:none">View on PlanFind &rarr;</a>'
        if a.get("council_url"):
            links += f' &nbsp;&middot;&nbsp; <a href="{_esc(a["council_url"])}" style="color:#5a6a7e;text-decoration:none">Council page</a>'
        cards += f"""
        <tr><td style="padding:0 0 12px 0">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #dde4ed;border-radius:12px">
            <tr><td style="padding:16px 18px">
              <span style="display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:{fg};background:{bg};border-radius:999px;padding:3px 10px">{label}</span>
              <span style="font-size:12px;color:#5a6a7e;margin-left:8px">{_esc(a.get('application_type') or '')}</span>
              <div style="font-size:16px;font-weight:700;color:#1a2332;margin:10px 0 2px 0">{_esc(a.get('address') or 'Address not shown')}</div>
              <div style="font-size:12px;color:#5a6a7e;margin-bottom:8px">{_esc(a.get('reference') or '')} &middot; {_days_ago(a.get('submitted_date'))}</div>
              <div style="font-size:14px;line-height:1.5;color:#374151;margin-bottom:12px">{_esc(text)}</div>
              <div style="font-size:13px">{links}</div>
            </td></tr>
          </table>
        </td></tr>"""

    more = ""
    if count > MAX_IN_EMAIL:
        more = f'<p style="font-size:13px;color:#5a6a7e;margin:4px 0 0 0">Plus {count - MAX_IN_EMAIL} more &ndash; <a href="{search_url}" style="color:#1a5fa8">see them all on PlanFind</a>.</p>'

    html = f"""
    <div style="background:#f4f7fb;padding:24px 12px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto">
        <tr><td style="background:#0e3d6e;border-radius:14px 14px 0 0;padding:22px 24px">
          <div style="font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-.01em">PlanFind</div>
          <div style="font-size:13px;color:#b9d3f0;margin-top:2px">Planning applications near you</div>
        </td></tr>
        <tr><td style="background:#e8f1fb;padding:18px 24px">
          <div style="font-size:20px;font-weight:800;color:#0e3d6e">{count} new planning application{plural}</div>
          <div style="font-size:14px;color:#374151;margin-top:2px">near <strong>{postcode}</strong>, within {_esc(sub.get('radius_miles') or '')} mile{'' if str(sub.get('radius_miles')) in ('1', '1.0') else 's'}</div>
        </td></tr>
        <tr><td style="padding:18px 0 0 0">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{cards}</table>
          {more}
          <p style="margin:18px 0 6px 0"><a href="{search_url}" style="display:inline-block;background:#2e8b3a;color:#ffffff;font-size:14px;font-weight:700;text-decoration:none;border-radius:10px;padding:12px 22px">See everything near {postcode}</a></p>
        </td></tr>
        <tr><td style="padding:18px 4px 0 4px;font-size:12px;color:#8a97a8;line-height:1.5">
          You get this because you set up an alert for {postcode} on PlanFind. Dates show when each application was submitted to the council.<br>
          <a href="{unsub_url}" style="color:#8a97a8">Unsubscribe</a>
        </td></tr>
      </table>
    </div>
    """
    subject = f"{count} new planning application{plural} near {sub.get('postcode') or 'you'}"
    return subject, html, unsub_url


async def _fetch_apps(ids: list) -> list:
    flt = f"in.({','.join(str(i) for i in ids[:20])})"
    try:
        return await _get("planning_applications", select=_SELECT + ",ai_summary", id=flt)
    except Exception:
        return await _get("planning_applications", select=_SELECT, id=flt)   # no ai_summary column: plain descriptions


async def dispatch_alerts() -> None:
    print(f"PlanFind alert dispatcher - {date.today().isoformat()}")
    print(f"SUPABASE_URL: {'set' if SUPABASE_URL else 'NOT SET'}")
    print(f"RESEND_API_KEY: {'set' if RESEND_API_KEY else 'NOT SET (dry-run mode)'}")

    subs = await _get("alert_subscriptions",
                      select="id,email,postcode,lat,lng,radius_miles,unsubscribe_token", confirmed="eq.true")
    print(f"\n{len(subs)} confirmed subscriber(s)\n")
    sent = failed = 0

    for sub in subs:
        sub_id, email = sub["id"], sub["email"]
        radius = float(sub["radius_miles"])
        print(f"  [{email}] {sub.get('postcode', '')} r={radius}mi")
        try:
            nearby = await _rpc("applications_near", {"p_lat": sub["lat"], "p_lng": sub["lng"],
                                                      "p_miles": radius, "p_days_back": DAYS_BACK})
        except Exception as e:
            print(f"    RPC error: {e}")
            continue
        if not nearby:
            print(f"    No nearby applications in the last {DAYS_BACK} days - skip")
            continue

        app_ids = [r["application_id"] for r in nearby]
        try:
            already = await _get("alert_log", select="application_id", subscription_id=f"eq.{sub_id}",
                                 application_id=f"in.({','.join(str(i) for i in app_ids)})")
        except Exception as e:
            print(f"    alert_log check error: {e} - skipping this subscriber to avoid sending duplicates")
            continue
        alerted = {r["application_id"] for r in already}
        new_ids = [i for i in app_ids if i not in alerted]
        if not new_ids:
            print(f"    All {len(app_ids)} nearby app(s) already alerted")
            continue
        print(f"    {len(new_ids)} new app(s) to send (of {len(app_ids)} nearby)")

        try:
            apps = await _fetch_apps(new_ids)
        except Exception as e:
            print(f"    App fetch error: {e}")
            continue
        if not apps:
            continue

        subject, body_html, unsub_url = _build_email(sub, apps)
        if await _send_email(email, subject, body_html, unsub_url):
            sent += 1
            print(f"    Sent: {subject}")
            code = await _insert("alert_log", [{"subscription_id": sub_id, "application_id": a["id"]} for a in apps],
                                 upsert=True)
            print(f"    Logged {len(apps)} alert record(s) (HTTP {code})")
        elif RESEND_API_KEY:
            failed += 1
            print("    NOT logged as sent - will try again on the next run")

    print(f"\nDone. Emails sent: {sent}   failed: {failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(dispatch_alerts())
