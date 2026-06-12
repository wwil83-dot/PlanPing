#!/usr/bin/env python3
"""
PlanFind alert dispatcher — called by GitHub Actions.
Uses Supabase REST API exclusively (no asyncpg/TCP) to bypass GH Actions IP blocks.
"""
import asyncio
import os
import httpx
from datetime import date, timedelta

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "alerts@planfind.co.uk")
BASE_URL = os.environ.get("BASE_URL", "https://planfind.co.uk")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("SUPABASE_URL and SUPABASE_KEY must be set")

_API = f"{SUPABASE_URL}/rest/v1"
_H = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


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
        r = await c.post(
            f"{_API}/{table}", json=rows,
            headers={**_H, "Prefer": prefer}
        )
        return r.status_code


async def _send_email(to: str, subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        print(f"    [DRY RUN — no RESEND_API_KEY] Would email {to}: {subject}")
        return
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": FROM_EMAIL, "to": to, "subject": subject, "html": html},
        )
        if r.status_code not in (200, 201):
            print(f"    Email error {r.status_code}: {r.text[:200]}")


def _days_ago(iso_date: str) -> str:
    if not iso_date:
        return "Unknown date"
    try:
        d = date.fromisoformat(str(iso_date)[:10])
        delta = (date.today() - d).days
        if delta == 0: return "Today"
        if delta == 1: return "Yesterday"
        if delta < 7: return f"{delta} days ago"
        return d.strftime("%-d %b %Y")
    except Exception:
        return str(iso_date)[:10]


def _build_email(sub: dict, apps: list) -> tuple[str, str]:
    count = len(apps)
    postcode = sub.get("postcode", "your area")
    unsub_url = f"{BASE_URL}/unsubscribe/{sub['unsubscribe_token']}"

    rows_html = ""
    for app in apps[:10]:
        date_str = _days_ago(app.get("submitted_date"))
        council_url = app.get("council_url") or ""
        ref = app.get("reference", "N/A")
        link = (
            f'<a href="{council_url}" style="color:#1d4ed8">{ref}</a>'
            if council_url else ref
        )
        raw_desc = app.get("description") or ""
        desc = raw_desc[:120] + ("..." if len(raw_desc) > 120 else "")
        rows_html += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:14px">
            {link}<br>
            <span style="color:#374151;font-weight:500">{app.get('address', '')}</span><br>
            <span style="color:#6b7280;font-size:13px">{desc}</span>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;
                     color:#6b7280;white-space:nowrap">
            {app.get('application_type', '')}<br>{date_str}
          </td>
        </tr>"""

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <div style="background:#1d4ed8;padding:20px 24px">
        <h1 style="color:#fff;margin:0;font-size:20px">PlanFind</h1>
      </div>
      <div style="padding:24px">
        <p style="font-size:16px;color:#111827">
          <strong>{count} new planning application{'s' if count != 1 else ''}</strong>
          near <strong>{postcode}</strong>
        </p>
        <table style="width:100%;border-collapse:collapse;margin-top:16px">
          <thead>
            <tr style="background:#f9fafb">
              <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;
                         font-weight:500;text-transform:uppercase">Application</th>
              <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;
                         font-weight:500;text-transform:uppercase">Type / Date</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
        <p style="margin-top:20px">
          <a href="{BASE_URL}/search?postcode={postcode}"
             style="background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:6px;
                    text-decoration:none;font-size:14px">
            View all on PlanFind
          </a>
        </p>
      </div>
      <div style="padding:16px 24px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af">
        PlanFind &middot;
        <a href="{unsub_url}" style="color:#9ca3af">Unsubscribe</a>
      </div>
    </div>
    """
    subject = f"{count} new planning application{'s' if count != 1 else ''} near {postcode}"
    return subject, html


async def dispatch_alerts() -> None:
    print(f"PlanFind alert dispatcher — {date.today().isoformat()}")
    print(f"SUPABASE_URL: {'set' if SUPABASE_URL else 'NOT SET'}")
    print(f"RESEND_API_KEY: {'set' if RESEND_API_KEY else 'NOT SET (dry-run mode)'}")

    subs = await _get(
        "alert_subscriptions",
        select="id,email,postcode,lat,lng,radius_miles,unsubscribe_token",
        confirmed="eq.true",
    )
    print(f"\n{len(subs)} confirmed subscriber(s)\n")

    for sub in subs:
        sub_id = sub["id"]
        email = sub["email"]
        lat, lng = sub["lat"], sub["lng"]
        radius = float(sub["radius_miles"])
        print(f"  [{email}] {sub.get('postcode','')} r={radius}mi")

        try:
            nearby = await _rpc("applications_near", {
                "p_lat": lat, "p_lng": lng,
                "p_miles": radius, "p_days_back": 7,
            })
        except Exception as e:
            print(f"    RPC error: {e}")
            continue

        if not nearby:
            print("    No nearby applications this week — skip")
            continue

        app_ids = [r["application_id"] for r in nearby]
        id_filter = f"in.({','.join(str(i) for i in app_ids)})"

        try:
            already = await _get(
                "alert_log",
                select="application_id",
                subscription_id=f"eq.{sub_id}",
                application_id=id_filter,
            )
        except Exception as e:
            print(f"    alert_log check error: {e}")
            already = []

        alerted_ids = {r["application_id"] for r in already}
        new_ids = [i for i in app_ids if i not in alerted_ids]

        if not new_ids:
            print(f"    All {len(app_ids)} nearby app(s) already alerted")
            continue

        print(f"    {len(new_ids)} new app(s) to send (of {len(app_ids)} nearby)")

        new_id_filter = f"in.({','.join(str(i) for i in new_ids[:20])})"
        try:
            apps = await _get(
                "planning_applications",
                select="id,reference,address,description,application_type,status,submitted_date,council_url",
                id=new_id_filter,
            )
        except Exception as e:
            print(f"    App fetch error: {e}")
            continue

        if not apps:
            continue

        subject, html = _build_email(sub, apps)
        await _send_email(email, subject, html)
        print(f"    Sent: {subject}")

        log_rows = [
            {"subscription_id": sub_id, "application_id": a["id"]}
            for a in apps
        ]
        await _insert("alert_log", log_rows, upsert=True)
        print(f"    Logged {len(log_rows)} alert record(s)")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(dispatch_alerts())
