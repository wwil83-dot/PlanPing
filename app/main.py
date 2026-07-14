"""
PlanPing — FastAPI backend
Run with: uvicorn app.main:app --reload
"""
import os
from datetime import datetime, date
from typing import Optional
from jinja2 import Environment, FileSystemLoader

from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_db, lifespan
from app.geocode import postcode_lookup

app = FastAPI(lifespan=lifespan, title="PlanPing")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_jinja = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)


def render(template: str, ctx: dict) -> HTMLResponse:
    return HTMLResponse(_jinja.get_template(template).render(**ctx))


# ─────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    async with get_db() as db:
        # NOTE: requires app_count > 0, not just coverage_source set — a
        # council can have coverage_source='idox_scraper' left over from a
        # past attempt (e.g. its portal broke, got commented out of the
        # active scraper list) while genuinely having zero applications.
        # Without this check such councils silently count toward the
        # headline stat forever. See Bury/Durham, fixed 2026-07-09.
        council_count = await db.fetchval("""
            SELECT COUNT(*) FROM councils c
            WHERE c.active = true
            AND c.coverage_source IN
            ('idox_scraper','arcus_scraper','data_gov_uk','gov_api','northgate_scraper')
            AND EXISTS (
                SELECT 1 FROM planning_applications pa
                WHERE pa.council_id = c.id
            )
        """)
        app_count = await db.fetchval(
            "SELECT COUNT(*) FROM planning_applications"
        )
    return render("index.html", {
        "request": request,
        "council_count": council_count,
        "app_count": app_count,
    })


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, postcode: str, radius: float = 1.0, days: int = 30):
    postcode = postcode.strip().upper()
    location = await postcode_lookup(postcode)

    if not location:
        # BUG FIX (2026-07-14): this branch used to render index.html without
        # app_count/council_count, which index.html always references to
        # build the stats banner — Jinja2 hit an Undefined value and crashed
        # with a 500 on EVERY failed postcode lookup, not just this one.
        # Fetch the same stats index() uses so the error page renders
        # correctly instead of blowing up.
        async with get_db() as db:
            council_count = await db.fetchval("""
                SELECT COUNT(*) FROM councils c
                WHERE c.active = true
                AND c.coverage_source IN
                ('idox_scraper','arcus_scraper','data_gov_uk','gov_api','northgate_scraper')
                AND EXISTS (
                    SELECT 1 FROM planning_applications pa
                    WHERE pa.council_id = c.id
                )
            """)
            app_count = await db.fetchval(
                "SELECT COUNT(*) FROM planning_applications"
            )
        return render("index.html", {
            "request": request,
            "error": f"Could not find postcode '{postcode}'. Please check and try again.",
            "postcode": postcode,
            "council_count": council_count,
            "app_count": app_count,
        })

    lat, lng = location["lat"], location["lng"]
    council_name = location.get("council", "")

    async with get_db() as db:
        # Find applications near this postcode
        rows = await db.fetch("""
            SELECT
                a.id, a.reference, a.address, a.postcode,
                a.description, a.application_type, a.status,
                a.submitted_date, a.decision_date, a.council_url,
                c.name AS council_name, c.slug AS council_slug,
                c.coverage_source,
                an.distance_miles
            FROM applications_near($1, $2, $3, $4) an
            JOIN planning_applications a ON a.id = an.application_id
            JOIN councils c ON c.id = a.council_id
            ORDER BY a.submitted_date DESC NULLS LAST, an.distance_miles
        """, lat, lng, radius, days)

        # Check if this council is covered
        council = await db.fetchrow("""
            SELECT id, name, slug, coverage_source, portal_url, system
            FROM councils
            WHERE name ILIKE $1
               OR name ILIKE $2
            LIMIT 1
        """, f"%{council_name}%", f"{council_name}%")

    applications = [dict(r) for r in rows]
    for a in applications:
        a["distance_miles"] = round(a["distance_miles"], 1)
        a["type_badge"] = _type_badge(a.get("application_type", ""))
        a["is_major"] = _is_major(a.get("application_type", ""))
        a["status_class"] = _status_class(a.get("status", ""))
        a["days_ago"] = _days_ago(a.get("submitted_date"))

    coverage = _coverage_message(council, council_name)

    return render("results.html", {
        "request": request,
        "postcode": postcode,
        "radius": radius,
        "days": days,
        "applications": applications,
        "total": len(applications),
        "lat": lat,
        "lng": lng,
        "council": dict(council) if council else None,
        "council_name": council_name,
        "coverage": coverage,
    })


@app.get("/application/{app_id}", response_class=HTMLResponse)
async def application_detail(request: Request, app_id: int):
    async with get_db() as db:
        row = await db.fetchrow("""
            SELECT a.*, c.name AS council_name, c.slug AS council_slug, c.portal_url
            FROM planning_applications a
            JOIN councils c ON c.id = a.council_id
            WHERE a.id = $1
        """, app_id)
        if not row:
            raise HTTPException(404, "Application not found")

    return render("application.html", {
        "request": request,
        "app": dict(row),
    })


@app.get("/council/{slug}", response_class=HTMLResponse)
async def council_page(request: Request, slug: str):
    async with get_db() as db:
        council = await db.fetchrow(
            "SELECT * FROM councils WHERE slug=$1", slug
        )
        if not council:
            raise HTTPException(404, "Council not found")

        recent = await db.fetch("""
            SELECT id, reference, address, description,
                   application_type, status, submitted_date,
                   lat, lng
            FROM planning_applications
            WHERE council_id = $1
            ORDER BY submitted_date DESC NULLS LAST
            LIMIT 50
        """, council["id"])

    apps = [dict(r) for r in recent]
    for a in apps:
        a["type_badge"] = _type_badge(a.get("application_type", ""))
        a["is_major"] = _is_major(a.get("application_type", ""))
        a["is_mapped"] = a.get("lat") is not None
        a["days_ago"] = _days_ago(a.get("submitted_date"))

    return render("council.html", {
        "request": request,
        "council": dict(council),
        "recent": apps,
    })


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return render("about.html", {"request": request})


@app.get("/councils", response_class=HTMLResponse)
async def councils_list(request: Request):
    async with get_db() as db:
        # PERFORMANCE FIX (2026-07-14): the old version ran TWO correlated
        # subqueries PER COUNCIL ROW (a COUNT and a MAX against
        # planning_applications, which has grown to 100,000+ rows from
        # months of nightly scraping). With ~250 council rows that's 500+
        # separate scans of a huge table — this got slower every single day
        # as more data accumulated, independent of adding new councils. A
        # single LEFT JOIN + GROUP BY does the same job in one table scan.
        councils = await db.fetch("""
            SELECT c.name, c.slug, c.region, c.system, c.coverage_source, c.portal_url,
                   COUNT(pa.id) AS app_count,
                   MAX(pa.submitted_date) AS latest_date
            FROM councils c
            LEFT JOIN planning_applications pa ON pa.council_id = c.id
            WHERE c.active = TRUE
            GROUP BY c.id, c.name, c.slug, c.region, c.system, c.coverage_source, c.portal_url
            ORDER BY c.name
        """)

    # NOTE: "covered" requires app_count > 0, not just a coverage_source
    # outside the pending/none/manual_link set. A council's coverage_source
    # can get set to e.g. 'idox_scraper' once and then never reset if its
    # portal later breaks and it gets commented out of the active scraper
    # list — without the app_count check it would show "Live" forever with
    # an empty results page. See Bury/Durham, fixed 2026-07-09.
    covered = [
        c for c in councils
        if c["coverage_source"] not in ("pending", "none", "manual_link")
        and c["app_count"] > 0
    ]

    # Councils we don't scrape but have a known, real portal_url for — these
    # get a direct external link on the coverage page so people can at least
    # find their council's own site easily, even though we can't show live
    # data for it. coverage_source='manual_link' + a non-null portal_url is
    # the signal for this bucket. Added 2026-07-12.
    manual_link = [
        c for c in councils
        if c not in covered
        and c["coverage_source"] == "manual_link"
        and c["portal_url"]
    ]

    # Everything else — genuinely no known link yet, or coverage_source is
    # still 'pending'/'none'.
    pending = [c for c in councils if c not in covered and c not in manual_link]

    return render("councils.html", {
        "request": request,
        "covered": covered,
        "manual_link": manual_link,
        "pending": pending,
        "total": len(councils),
        "covered_count": len(covered),
    })


# ─────────────────────────────────────────────
# API — Alert subscriptions
# ─────────────────────────────────────────────

@app.post("/api/alert")
async def create_alert(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    postcode: str = Form(...),
    radius_miles: int = Form(1),
    frequency: str = Form("weekly"),
):
    postcode = postcode.strip().upper()
    location = await postcode_lookup(postcode)
    if not location:
        raise HTTPException(400, "Invalid postcode")

    async with get_db() as db:
        existing = await db.fetchval(
            "SELECT id FROM alert_subscriptions WHERE email=$1 AND postcode=$2",
            email, postcode
        )
        if existing:
            return {"ok": True, "message": "You already have an alert for this postcode."}

        await db.execute("""
            INSERT INTO alert_subscriptions
                (email, postcode, lat, lng, radius_miles, frequency)
            VALUES ($1,$2,$3,$4,$5,$6)
        """, email, postcode, location["lat"], location["lng"],
            min(radius_miles, 5), frequency)

    from app.alerts import send_confirmation
    background_tasks.add_task(send_confirmation, email, postcode)

    return {"ok": True, "message": "Check your email to confirm your alert."}


@app.post("/api/waitlist")
async def join_waitlist(
    email: str = Form(...),
    postcode: str = Form(...),
    council_id: int = Form(...),
):
    async with get_db() as db:
        await db.execute("""
            INSERT INTO coverage_waitlist (email, postcode, council_id)
            VALUES ($1,$2,$3)
            ON CONFLICT DO NOTHING
        """, email, postcode, council_id)
    return {"ok": True, "message": "We'll notify you when your council is covered."}


@app.get("/confirm/{token}", response_class=HTMLResponse)
async def confirm(request: Request, token: str):
    async with get_db() as db:
        result = await db.execute("""
            UPDATE alert_subscriptions SET confirmed=TRUE
            WHERE confirm_token=$1 AND confirmed=FALSE
        """, token)
    return render("confirm.html", {
        "request": request,
        "confirmed": result != "UPDATE 0"
    })


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(request: Request, token: str):
    async with get_db() as db:
        result = await db.execute(
            "DELETE FROM alert_subscriptions WHERE unsubscribe_token=$1", token
        )
    return render("unsubscribe.html", {
        "request": request,
        "removed": result != "DELETE 0"
    })


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _is_major(app_type: str) -> bool:
    """Flag application types that are significant developments."""
    t = (app_type or "").upper()
    major_keywords = ["OUTLINE", "OUT", "MAJOR", "EIA", "HYBRID",
                      "PERMISSION IN PRINCIPLE", "PIP", "TECHNICAL DETAILS"]
    return any(k in t for k in major_keywords)


def _type_badge(app_type: str) -> str:
    t = (app_type or "").lower()
    if "outline" in t or "/out" in t or t.endswith("out"):
        return "outline"
    if "householder" in t or "extension" in t:
        return "householder"
    if "full" in t:
        return "full"
    if "listed" in t:
        return "listed"
    if "tree" in t:
        return "tree"
    if "advertisement" in t or "advert" in t:
        return "advert"
    if "prior" in t:
        return "prior"
    if "major" in t or "eia" in t:
        return "major"
    return "other"


def _status_class(status: str) -> str:
    s = (status or "").lower()
    if s in ("approved", "granted", "permitted"):
        return "approved"
    if s in ("refused", "rejected"):
        return "refused"
    if s in ("withdrawn",):
        return "withdrawn"
    return "pending"


def _days_ago(submitted_date) -> str:
    if not submitted_date:
        return "Unknown date"
    if isinstance(submitted_date, str):
        try:
            submitted_date = date.fromisoformat(submitted_date)
        except Exception:
            return submitted_date
    delta = (date.today() - submitted_date).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if delta < 7:
        return f"{delta} days ago"
    if delta < 30:
        weeks = delta // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    return submitted_date.strftime("%-d %b %Y")


def _coverage_message(council, council_name: str) -> dict:
    if not council:
        return {
            "type": "unknown",
            "message": f"We couldn't identify your council from postcode data.",
        }

    source = council["coverage_source"]
    name = council["name"]
    portal = council["portal_url"] or ""

    if source in ("idox_scraper", "arcus_scraper", "northgate_scraper", "gov_api", "data_gov_uk"):
        return {
            "type": "covered",
            "message": f"{name} is fully covered — results below are live.",
        }
    elif source == "manual_link":
        return {
            "type": "partial",
            "message": f"We don't yet scrape {name} automatically.",
            "portal_url": portal,
            "council_id": council["id"],
        }
    else:
        return {
            "type": "pending",
            "message": f"{name} coverage is coming soon.",
            "portal_url": portal,
            "council_id": council["id"],
        }
