"""
PlanFind alert system.
Sends confirmation emails and dispatches new application alerts.
"""
import os
import html as _html
import httpx
from app.db import get_db

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL     = os.environ.get("FROM_EMAIL", "alerts@planfind.co.uk")
BASE_URL       = (os.environ.get("BASE_URL", "https://www.planfind.co.uk") or "https://www.planfind.co.uk").rstrip("/")


async def _send(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print(f"[EMAIL] {to}: {subject}")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": FROM_EMAIL, "to": to, "subject": subject, "html": html},
            )
    except Exception as e:
        print(f"[EMAIL ERROR] {to}: {type(e).__name__}: {e}")
        return False
    if r.status_code not in (200, 201):
        print(f"[EMAIL ERROR] {to}: HTTP {r.status_code} {r.text[:200]}")
        return False
    return True


async def send_confirmation(email: str, postcode: str):
    async with get_db() as db:
        row = await db.fetchrow(
            "SELECT confirm_token FROM alert_subscriptions WHERE email=$1 AND postcode=$2",
            email, postcode
        )
        if not row:
            return

    confirm_url = f"{BASE_URL}/confirm/{row['confirm_token']}"
    pc = _html.escape(postcode or "", quote=True)
    # Redesigned 2026-11-25: same look as the alert emails (see dispatch_alerts.py).
    html = f"""
    <div style="background:#f4f7fb;padding:24px 12px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto">
        <tr><td style="background:#0e3d6e;border-radius:14px 14px 0 0;padding:22px 24px">
          <div style="font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-.01em">PlanFind</div>
          <div style="font-size:13px;color:#b9d3f0;margin-top:2px">Planning applications near you</div>
        </td></tr>
        <tr><td style="background:#ffffff;border:1px solid #dde4ed;border-top:0;border-radius:0 0 14px 14px;padding:26px 24px">
          <div style="font-size:20px;font-weight:800;color:#0e3d6e;margin-bottom:10px">One click to confirm your alert</div>
          <p style="font-size:15px;line-height:1.55;color:#374151;margin:0 0 18px 0">
            You asked to be told when new planning applications are submitted near
            <strong>{pc}</strong>. Confirm below and we'll email you each time something new appears, with the
            address, what is proposed and where it stands.
          </p>
          <p style="margin:0 0 20px 0"><a href="{confirm_url}" style="display:inline-block;background:#2e8b3a;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px;padding:13px 26px">Confirm my alert</a></p>
          <p style="font-size:13px;line-height:1.5;color:#8a97a8;margin:0">
            If you didn't ask for this, just ignore this email and nothing will be sent.<br>
            PlanFind &middot; <a href="{BASE_URL}" style="color:#8a97a8">planfind.co.uk</a>
          </p>
        </td></tr>
      </table>
    </div>
    """
    await _send(email, f"Confirm your PlanFind alert for {postcode}", html)


async def send_waitlist_confirmation(email: str, tier: str):
    """2026-11-26: confirmation for the pricing-page waitlist (sent once, when the address is first added)."""
    plan = {"plus": "PlanFind Plus", "professional": "PlanFind Professional", "both": "PlanFind Plus and PlanFind Professional"}.get(tier, "PlanFind Professional")
    plan = _html.escape(plan, quote=True)
    html = f"""
    <div style="background:#f4f7fb;padding:24px 12px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto">
        <tr><td style="background:#0e3d6e;border-radius:14px 14px 0 0;padding:22px 24px">
          <div style="font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-.01em">PlanFind</div>
          <div style="font-size:13px;color:#b9d3f0;margin-top:2px">Planning applications near you</div>
        </td></tr>
        <tr><td style="background:#ffffff;border:1px solid #dde4ed;border-top:0;border-radius:0 0 14px 14px;padding:26px 24px">
          <div style="font-size:20px;font-weight:800;color:#0e3d6e;margin-bottom:10px">You're on the waitlist</div>
          <p style="font-size:15px;line-height:1.55;color:#374151;margin:0 0 14px 0">
            Thanks for your interest in <strong>{plan}</strong>. We'll email you once, when the plan opens.
            There's nothing to pay and nothing more you need to do.
          </p>
          <p style="font-size:15px;line-height:1.55;color:#374151;margin:0 0 20px 0">
            Searching planning applications and email alerts near your home stay free.
          </p>
          <p style="margin:0 0 20px 0"><a href="{BASE_URL}/postcode-search" style="display:inline-block;background:#2e8b3a;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;padding:12px 22px;border-radius:10px">Search planning applications</a></p>
          <p style="font-size:13px;line-height:1.5;color:#8a97a8;margin:0">
            Didn't sign up, or want your address removed? Tell us at <a href="{BASE_URL}/about#contact" style="color:#8a97a8">planfind.co.uk/about</a> and we'll delete it.<br>
            PlanFind &middot; <a href="{BASE_URL}" style="color:#8a97a8">planfind.co.uk</a>
          </p>
        </td></tr>
      </table>
    </div>
    """
    await _send(email, "You're on the PlanFind waitlist", html)


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
              <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:500;text-transform:uppercase">Application</th>
              <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:500;text-transform:uppercase">Type / Date</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
        <p style="margin-top:20px">
          <a href="{BASE_URL}/search?postcode={postcode}"
             style="background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-size:14px">
            View all on PlanFind
          </a>
        </p>
      </div>
      <div style="padding:16px 24px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af">
        PlanFind · <a href="{unsub_url}" style="color:#9ca3af">Unsubscribe</a>
      </div>
    </div>
    """

    subject = f"{count} new planning application{'s' if count != 1 else ''} near {postcode}"
    await _send(sub["email"], subject, html)