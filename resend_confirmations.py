#!/usr/bin/env python3
"""
PlanFind - one-off: re-send the confirmation email to people whose alert sign-up never got confirmed.
Why: until 2026-11-25 the website sent from Resend's test address, which only delivers to the account owner, so every
confirmation to anyone else was refused. Each person asked for the alert, so this is a single transactional follow-up.
Sends ONE email per unconfirmed subscription; nothing else is ever sent to them unless they confirm.

Env: SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, FROM_EMAIL, BASE_URL, DRY_RUN (1 = only list who would get it; default 1)
"""
import asyncio
import html as _html
import os
import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")
BASE_URL = (os.environ.get("BASE_URL", "https://www.planfind.co.uk") or "https://www.planfind.co.uk").rstrip("/")
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def build(postcode: str, token: str) -> str:
    pc = _html.escape(postcode or "", quote=True)
    url = f"{BASE_URL}/confirm/{token}"
    return f"""
    <div style="background:#f4f7fb;padding:24px 12px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto">
        <tr><td style="background:#0e3d6e;border-radius:14px 14px 0 0;padding:22px 24px">
          <div style="font-size:22px;font-weight:800;color:#ffffff">PlanFind</div>
          <div style="font-size:13px;color:#b9d3f0;margin-top:2px">Planning applications near you</div>
        </td></tr>
        <tr><td style="background:#ffffff;border:1px solid #dde4ed;border-top:0;border-radius:0 0 14px 14px;padding:26px 24px">
          <div style="font-size:20px;font-weight:800;color:#0e3d6e;margin-bottom:10px">Sorry &ndash; please confirm your alert</div>
          <p style="font-size:15px;line-height:1.55;color:#374151;margin:0 0 14px 0">
            You asked PlanFind to email you about new planning applications near <strong>{pc}</strong>.
            Our first confirmation email didn't reach you because of a fault on our side, and we're sorry about that.
          </p>
          <p style="font-size:15px;line-height:1.55;color:#374151;margin:0 0 18px 0">
            One click below switches your alert on. If you no longer want it, just ignore this email and we won't send anything else.
          </p>
          <p style="margin:0 0 20px 0"><a href="{url}" style="display:inline-block;background:#2e8b3a;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px;padding:13px 26px">Confirm my alert</a></p>
          <p style="font-size:13px;line-height:1.5;color:#8a97a8;margin:0">PlanFind &middot; <a href="{BASE_URL}" style="color:#8a97a8">planfind.co.uk</a></p>
        </td></tr>
      </table>
    </div>"""


async def main():
    if not (SUPABASE_URL and SUPABASE_KEY):
        raise SystemExit("SUPABASE_URL / SUPABASE_KEY not set")
    if not DRY_RUN and not (RESEND_API_KEY and FROM_EMAIL):
        raise SystemExit("RESEND_API_KEY and FROM_EMAIL must be set to send")
    if not DRY_RUN and "resend.dev" in FROM_EMAIL.lower():
        raise SystemExit("FROM_EMAIL is still Resend's test address (resend.dev): set it to your own verified domain first")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/alert_subscriptions", headers=H,
                        params={"select": "id,email,postcode,confirm_token", "confirmed": "eq.false", "order": "id"})
        r.raise_for_status()
        rows = r.json()
        print(f"{len(rows)} unconfirmed subscription(s)   DRY_RUN={DRY_RUN}\n")
        sent = failed = 0
        for row in rows:
            tok = row.get("confirm_token")
            if not tok:
                print(f"  skip {row['email']} {row['postcode']}: no confirm token")
                continue
            if DRY_RUN:
                print(f"  would email {row['email']}  ({row['postcode']})")
                continue
            resp = await c.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                                json={"from": FROM_EMAIL, "to": row["email"],
                                      "subject": f"Sorry - please confirm your PlanFind alert for {row['postcode']}",
                                      "html": build(row["postcode"], tok)})
            if resp.status_code in (200, 201):
                sent += 1
                print(f"  sent  {row['email']}  ({row['postcode']})")
            else:
                failed += 1
                print(f"  FAILED {row['email']}: HTTP {resp.status_code} {resp.text[:150]}")
            await asyncio.sleep(0.6)
    print(f"\nDone. sent {sent}, failed {failed}" + ("   (DRY RUN: nothing sent)" if DRY_RUN else ""))
    if failed:
        raise SystemExit(1)

asyncio.run(main())
