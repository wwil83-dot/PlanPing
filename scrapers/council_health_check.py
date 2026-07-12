#!/usr/bin/env python3
"""
PlanFind — council health check.

Queries Supabase for Idox-scraped councils that have returned zero
applications for several consecutive nightly runs, and emails a digest if
any are found. A single empty run is normal (many low-volume councils
legitimately return 0 some nights) — this only fires once a council crosses
a sustained silence threshold, which is a much stronger signal that
something actually broke (WAF started blocking the scraper's IP, the
council migrated off Idox, a URL changed, etc.).

Intended to run weekly via GitHub Actions (see scrape.yml: council_health
job). Sends nothing if no council currently exceeds the threshold — the
whole point is to be silent when things are fine, so a real alert actually
stands out.
"""
import os
import sys
from datetime import datetime, timezone

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

# A council needs this many CONSECUTIVE empty nightly runs before it's
# flagged. 5 was chosen as a reasonable default — enough to rule out one-off
# bad luck (a single WAF hiccup, a temporary server outage) while still
# catching real breakage within about a week, since the scraper runs nightly.
EMPTY_RUN_THRESHOLD = int(os.environ.get("EMPTY_RUN_THRESHOLD", "5"))


def _h():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


async def _fetch_unhealthy_councils() -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/councils",
            params={
                "select": "id,name,portal_url,consecutive_empty_runs,last_saved_at,last_scraped_at",
                "coverage_source": "eq.idox_scraper",
                "active": "eq.true",
                "consecutive_empty_runs": f"gte.{EMPTY_RUN_THRESHOLD}",
                "order": "consecutive_empty_runs.desc",
            },
            headers=_h(),
        )
        r.raise_for_status()
        return r.json()


def _format_days_since(iso_timestamp: str | None) -> str:
    if not iso_timestamp:
        return "never"
    try:
        then = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - then).days
        if days == 0:
            return "today"
        if days == 1:
            return "1 day ago"
        return f"{days} days ago"
    except Exception:
        return iso_timestamp


def _build_email_html(unhealthy: list[dict]) -> str:
    rows = ""
    for c in unhealthy:
        name = c["name"]
        streak = c["consecutive_empty_runs"]
        last_saved = _format_days_since(c.get("last_saved_at"))
        portal = c.get("portal_url") or ""
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e5e5">{name}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e5e5;text-align:center">{streak}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e5e5">{last_saved}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e5e5">
            <a href="{portal}">{portal}</a>
          </td>
        </tr>
        """

    return f"""
    <div style="font-family:sans-serif;max-width:700px">
      <h2>PlanFind — {len(unhealthy)} council{'s' if len(unhealthy) != 1 else ''} may need attention</h2>
      <p>These councils have returned zero applications for at least
      {EMPTY_RUN_THRESHOLD} consecutive nightly scrapes. Worth checking
      whether the portal is still live, still Idox, or blocking the
      scraper's IP.</p>
      <table style="border-collapse:collapse;width:100%;font-size:14px">
        <thead>
          <tr style="background:#f5f5f5;text-align:left">
            <th style="padding:8px 12px">Council</th>
            <th style="padding:8px 12px;text-align:center">Empty runs</th>
            <th style="padding:8px 12px">Last saved data</th>
            <th style="padding:8px 12px">Portal</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


async def _send_email(html: str, count: int):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_EMAIL,
                "to": ADMIN_EMAIL,
                "subject": f"PlanFind: {count} council{'s' if count != 1 else ''} silently broken",
                "html": html,
            },
        )
        if r.status_code not in (200, 201):
            print(f"✗ Failed to send health digest: HTTP {r.status_code}: {r.text[:300]}")
        else:
            print(f"✓ Health digest sent to {ADMIN_EMAIL}")


async def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    if not RESEND_API_KEY or not FROM_EMAIL or not ADMIN_EMAIL:
        print("ERROR: Set RESEND_API_KEY, FROM_EMAIL, and ADMIN_EMAIL")
        sys.exit(1)

    unhealthy = await _fetch_unhealthy_councils()

    if not unhealthy:
        print(f"All councils healthy (threshold: {EMPTY_RUN_THRESHOLD} consecutive empty runs). No email sent.")
        return

    print(f"Found {len(unhealthy)} council(s) with >= {EMPTY_RUN_THRESHOLD} consecutive empty runs:")
    for c in unhealthy:
        print(f"  - {c['name']}: {c['consecutive_empty_runs']} empty runs, last saved {_format_days_since(c.get('last_saved_at'))}")

    html = _build_email_html(unhealthy)
    await _send_email(html, len(unhealthy))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
