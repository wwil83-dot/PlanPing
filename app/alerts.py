"""
PlanPing alert system.
Sends confirmation emails and dispatches new application alerts.
"""
import os
import httpx
from app.db import get_db

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL     = os.environ.get("FROM_EMAIL", "alerts@planping.co.uk")
BASE_URL       = os.environ.get("BASE_URL", "https://planping.onrender.com")


async def _send(to: str, subject: str, html: str):
    if not RESEND_API_KEY:
        print(f"[EMAIL] {to}: {subject}")
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": FROM_EMAIL, "to": to, "subject": subject, "html": html},
        )


async def send_confirmation(email: str, postcode: str):
    async with get_db() as db:
        row = await db.fetchrow(
            "SELECT confirm_token FROM alert_subscriptions WHERE email=$1 AND postcode=$2",
            email, postcode
        )
        if not row:
            return

    confirm_url = f"{BASE_URL}/confirm/{row['confirm_token']}"
    html = f"""
    <p>Hi,</p>
    <p>You asked to be alerted when new planning applications are submitted near
    <strong>{postcode}</strong>.</p>
    <p><a href="{confirm_url}" style="background:#1d4ed8;color:#fff;padding:10px 20px;
    border-radius:6px;text-decoration:none;display:inline-block;margin:12px 0">
    Confirm my alert</a></p>
    <p style="color:#6b7280;font-size:13px">
    If you didn't request this, just ignore this email.<br>
    PlanPing · <a href="{BASE_URL}">planping.co.uk</a>
    </p>
    """
    await _send(email, f"Confirm your PlanPing alert for {postcode}", html)


async def dispatch_alerts():
    """
    Find new applications that subscribed users haven't been alerted about.
    Runs after each scrape cycle.
    """
    async with get_db() as db:
        # Find all confirmed subscribers
        subs = await db.fetch("""
            SELECT id, email, lat, lng, radius_miles, frequency, unsubscribe_token
            FROM alert_subscriptions
            WHERE confirmed = TRUE
        """)

        for sub in subs:
            # Find new applications within radius not yet alerted
            new_apps = await db.fetch("""
                SELECT
                    a.id, a.reference, a.address, a.description,
                    a.application_type, a.status, a.submitted_date,
                    a.council_url, c.name AS council_name,
                    (
                        3959 * acos(LEAST(1.0,
                            cos(radians($2)) * cos(radians(a.lat)) *
                            cos(radians(a.lng) - radians($3)) +
                            sin(radians($2)) * sin(radians(a.lat))
                        ))
                    ) AS distance_miles
                FROM planning_applications a
                JOIN councils c ON c.id = a.council_id
                WHERE a.lat IS NOT NULL
                  AND a.submitted_date >= CURRENT_DATE - 7
                  AND (
                      3959 * acos(LEAST(1.0,
                          cos(radians($2)) * cos(radians(a.lat)) *
                          cos(radians(a.lng) - radians($3)) +
                          sin(radians($2)) * sin(radians(a.lat))
                      ))
                  ) <= $4
                  AND NOT EXISTS (
                      SELECT 1 FROM alert_log al
                      WHERE al.subscription_id = $1
                        AND al.application_id = a.id
                  )
                ORDER BY a.submitted_date DESC
                LIMIT 20
            """, sub["id"], sub["lat"], sub["lng"], sub["radius_miles"])

            if not new_apps:
                continue

            # Send digest
            await _send_alert_email(sub, list(new_apps))

            # Log sends
            for app in new_apps:
                await db.execute("""
                    INSERT INTO alert_log (subscription_id, application_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, sub["id"], app["id"])


async def _send_alert_email(sub: dict, apps: list):
    """Send a digest email for new applications."""
    count = len(apps)
    postcode = sub.get("postcode", "your area")
    unsub_url = f"{BASE_URL}/unsubscribe/{sub['unsubscribe_token']}"

    rows_html = ""
    for app in apps[:10]:
        date_str = app["submitted_date"].strftime("%-d %b %Y") if app.get("submitted_date") else "Unknown date"
        council_url = app.get("council_url", "")
        link = f'<a href="{council_url}" style="color:#1d4ed8">{app["reference"]}</a>' if council_url else app["reference"]
        rows_html += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:14px">
            {link}<br>
            <span style="color:#374151;font-weight:500">{app.get('address','')}</span><br>
            <span style="color:#6b7280;font-size:13px">{app.get('description','')[:120]}{'...' if len(app.get('description','')) > 120 else ''}</span>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#6b7280;white-space:nowrap">
            {app.get('application_type','')}<br>{date_str}
          </td>
        </tr>"""

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <div style="background:#1d4ed8;padding:20px 24px">
        <h1 style="color:#fff;margin:0;font-size:20px">PlanPing</h1>
      </div>
      <div style="padding:24px">
        <p style="font-size:16px;color:#111827">
          <strong>{count} new planning application{'s' if count != 1 else ''}</strong>
          near <strong>{postcode}</strong>
        </p>
        <table style="width:100%;border-collapse:collapse;margin-top:16px">
          <thead>
            <tr style="background:#f9fafb">
              <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:500;text-transform:uppercase">Application</th>
              <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:500;text-transform:uppercase">Type / Date</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
        <p style="margin-top:20px">
          <a href="{BASE_URL}/search?postcode={postcode}"
             style="background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-size:14px">
            View all on PlanPing
          </a>
        </p>
      </div>
      <div style="padding:16px 24px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af">
        PlanPing · <a href="{unsub_url}" style="color:#9ca3af">Unsubscribe</a>
      </div>
    </div>
    """

    subject = f"{count} new planning application{'s' if count != 1 else ''} near {postcode}"
    await _send(sub["email"], subject, html)
