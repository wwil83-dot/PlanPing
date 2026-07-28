"""
PlanPing — FastAPI backend
Run with: uvicorn app.main:app --reload
"""
import os
import csv
import io
from datetime import datetime, date
from typing import Optional
from jinja2 import Environment, FileSystemLoader

from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_db, lifespan
from app.geocode import postcode_lookup

app = FastAPI(lifespan=lifespan, title="PlanPing")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_jinja = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)


def render(template: str, ctx: dict) -> HTMLResponse:
    return HTMLResponse(_jinja.get_template(template).render(**ctx))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    async with get_db() as db:
        council_count = await db.fetchval("""
            SELECT COUNT(*) FROM councils c
            WHERE c.active = true
            AND c.coverage_source NOT IN ('pending', 'none', 'manual_link')
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


async def _fetch_applications(db, lat: float, lng: float, radius: float, days: int,
                               status: Optional[str] = None,
                               app_type: Optional[str] = None) -> list[dict]:
    rows = await db.fetch("""
        SELECT
            a.id, a.reference, a.address, a.postcode,
            a.description, a.application_type, a.status,
            a.submitted_date, a.decision_date, a.council_url,
            a.lat, a.lng,
            c.name AS council_name, c.slug AS council_slug,
            c.coverage_source,
            an.distance_miles
        FROM applications_near($1, $2, $3, $4) an
        JOIN planning_applications a ON a.id = an.application_id
        JOIN councils c ON c.id = a.council_id
        WHERE ($5::text IS NULL OR a.status = $5)
        ORDER BY a.submitted_date DESC NULLS LAST, an.distance_miles
    """, lat, lng, radius, days, status)

    applications = [dict(r) for r in rows]
    for a in applications:
        a["distance_miles"] = round(a["distance_miles"], 1)
        a["type_badge"] = _type_badge(a.get("application_type", ""))
        a["is_major"] = _is_major(a.get("application_type", ""))
        a["status_class"] = _status_class(a.get("status", ""))
        a["days_ago"] = _days_ago(a.get("submitted_date"))

    if app_type:
        applications = [a for a in applications if a["type_badge"] == app_type]

    _add_date_availability_flag(applications)
    return applications


TAG_META = {
    "large_site": {
        "title": "Large Site Developments",
        "intro": "Applications describing a significant number of dwellings/units, "
                  "a site measured in hectares, or explicitly flagged as a major "
                  "development.",
    },
    "farm_diversification": {
        "title": "Farm Diversification",
        "intro": "Agricultural or rural sites being converted, diversified, or put "
                  "to a new use — barn conversions, farm shops, holiday lets, and "
                  "similar.",
    },
    "commercial_conversion": {
        "title": "Commercial-to-Residential Conversion",
        "intro": "Offices, shops, retail units, or warehouses being converted to "
                  "residential use, including Permitted Development (Class MA/O) "
                  "prior approvals.",
    },
}


async def _fetch_tagged_applications(db, tag: str, status: Optional[str] = None,
                                      limit: int = 200) -> list[dict]:
    rows = await db.fetch("""
        SELECT
            a.id, a.reference, a.address, a.postcode,
            a.description, a.application_type, a.status,
            a.submitted_date, a.decision_date, a.council_url,
            c.name AS council_name, c.slug AS council_slug
        FROM planning_applications a
        JOIN councils c ON c.id = a.council_id
        WHERE a.tags @> ARRAY[$1]::text[]
        AND ($2::text IS NULL OR a.status = $2)
        ORDER BY a.submitted_date DESC NULLS LAST
        LIMIT $3
    """, tag, status, limit)

    applications = [dict(r) for r in rows]
    for a in applications:
        a["type_badge"] = _type_badge(a.get("application_type", ""))
        a["is_major"] = _is_major(a.get("application_type", ""))
        a["status_class"] = _status_class(a.get("status", ""))
        a["days_ago"] = _days_ago(a.get("submitted_date"))

    _add_date_availability_flag(applications)
    return applications


STATUS_FILTER_OPTIONS = ["pending", "approved", "refused", "withdrawn"]
TYPE_FILTER_OPTIONS = ["householder", "full", "outline", "listed", "tree",
                       "advert", "prior", "major", "other"]

# Councils whose real, live results view genuinely never displays a
# submission date at all — confirmed via direct raw-data evidence
# (2026-07-28), not a scraper bug we're still chasing. Applications from
# these councils will always show "Unknown date" here even though the
# application itself obviously has a real date — the council's own
# portal has it, our scraper's data source just doesn't expose it in
# this particular view. See arcus_scraper.py's module comments for the
# full investigation. Worth surfacing this honestly in the UI rather
# than let it look like a generic missing-data gap.
COUNCILS_WITHOUT_DATE_DATA = {
    "Powys County Council",
    "Erewash Borough Council",
    "Reading Borough Council",
    "Wrexham County Borough Council",
}


def _add_date_availability_flag(applications: list[dict]) -> None:
    """Mutates each application dict in place, adding
    'date_unavailable_note' — True only when we genuinely know the date
    is missing for a structural reason (council in the list above), not
    just because it hasn't been decided/scraped yet. Templates can use
    this to show a clear "check the council's own portal for the exact
    date" note instead of a bare, unexplained "Unknown date"."""
    for a in applications:
        a["date_unavailable_note"] = (
            a.get("submitted_date") is None
            and a.get("council_name") in COUNCILS_WITHOUT_DATE_DATA
        )



@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, postcode: str, radius: float = 1.0, days: int = 30,
                  status: Optional[str] = None, app_type: Optional[str] = None):
    postcode = postcode.strip().upper()
    location = await postcode_lookup(postcode)

    if not location:
        async with get_db() as db:
            council_count = await db.fetchval("""
                SELECT COUNT(*) FROM councils c
                WHERE c.active = true
                AND c.coverage_source NOT IN ('pending', 'none', 'manual_link')
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
        applications = await _fetch_applications(db, lat, lng, radius, days, status, app_type)

        council = await db.fetchrow("""
            SELECT id, name, slug, coverage_source, portal_url, system
            FROM councils
            WHERE name ILIKE $1
               OR name ILIKE $2
            LIMIT 1
        """, f"%{council_name}%", f"{council_name}%")

    map_markers = [
        {
            "id": a["id"],
            "lat": a["lat"],
            "lng": a["lng"],
            "reference": a.get("reference") or "",
            "address": a.get("address") or "",
            "is_centroid": a.get("geocode_quality") == "centroid",
        }
        for a in applications
        if a.get("lat") is not None and a.get("lng") is not None
    ]

    coverage = _coverage_message(council, council_name)

    return render("results.html", {
        "request": request,
        "postcode": postcode,
        "radius": radius,
        "days": days,
        "status": status,
        "app_type": app_type,
        "status_options": STATUS_FILTER_OPTIONS,
        "type_options": TYPE_FILTER_OPTIONS,
        "applications": applications,
        "map_markers": map_markers,
        "total": len(applications),
        "lat": lat,
        "lng": lng,
        "council": dict(council) if council else None,
        "council_name": council_name,
        "coverage": coverage,
    })


@app.get("/search.csv")
async def search_csv(postcode: str, radius: float = 1.0, days: int = 30,
                      status: Optional[str] = None, app_type: Optional[str] = None):
    postcode = postcode.strip().upper()
    location = await postcode_lookup(postcode)
    if not location:
        raise HTTPException(status_code=404, detail=f"Could not find postcode '{postcode}'")

    lat, lng = location["lat"], location["lng"]

    async with get_db() as db:
        applications = await _fetch_applications(db, lat, lng, radius, days, status, app_type)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Reference", "Council", "Address", "Postcode", "Description",
        "Application Type", "Status", "Submitted Date", "Decision Date",
        "Distance (miles)", "Council URL",
    ])
    for a in applications:
        writer.writerow([
            a.get("reference", ""), a.get("council_name", ""),
            a.get("address", ""), a.get("postcode", ""),
            a.get("description", ""), a.get("application_type", ""),
            a.get("status", ""),
            a.get("submitted_date").isoformat() if a.get("submitted_date") else "",
            a.get("decision_date").isoformat() if a.get("decision_date") else "",
            a.get("distance_miles", ""), a.get("council_url", ""),
        ])
    buffer.seek(0)

    filename = f"planfind_{postcode.replace(' ', '')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/bulk-search", response_class=HTMLResponse)
async def bulk_search_form(request: Request):
    return render("bulk_search.html", {
        "request": request,
        "postcodes_input": "",
        "radius": 1.0,
        "days": 30,
        "submitted": False,
        "results_by_postcode": {},
        "all_applications": [],
        "errors": [],
        "total": 0,
    })


@app.post("/bulk-search", response_class=HTMLResponse)
async def bulk_search(request: Request, postcodes: str = Form(...),
                       radius: float = Form(1.0), days: int = Form(30)):
    postcode_list = [p.strip().upper() for p in postcodes.splitlines() if p.strip()][:50]

    results_by_postcode: dict[str, list[dict]] = {}
    errors: list[str] = []
    all_applications: list[dict] = []
    seen_ids: set[int] = set()

    async with get_db() as db:
        for pc in postcode_list:
            location = await postcode_lookup(pc)
            if not location:
                errors.append(pc)
                continue

            apps = await _fetch_applications(db, location["lat"], location["lng"], radius, days)
            results_by_postcode[pc] = apps

            for a in apps:
                if a["id"] not in seen_ids:
                    seen_ids.add(a["id"])
                    all_applications.append(a)

    return render("bulk_search.html", {
        "request": request,
        "postcodes_input": postcodes,
        "radius": radius,
        "days": days,
        "submitted": True,
        "results_by_postcode": results_by_postcode,
        "all_applications": all_applications,
        "errors": errors,
        "total": len(all_applications),
    })


@app.post("/bulk-search.csv")
async def bulk_search_csv(postcodes: str = Form(...), radius: float = Form(1.0), days: int = Form(30)):
    postcode_list = [p.strip().upper() for p in postcodes.splitlines() if p.strip()][:50]

    all_applications: list[dict] = []
    seen_ids: set[int] = set()

    async with get_db() as db:
        for pc in postcode_list:
            location = await postcode_lookup(pc)
            if not location:
                continue
            apps = await _fetch_applications(db, location["lat"], location["lng"], radius, days)
            for a in apps:
                if a["id"] not in seen_ids:
                    seen_ids.add(a["id"])
                    all_applications.append(a)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Reference", "Council", "Address", "Postcode", "Description",
        "Application Type", "Status", "Submitted Date", "Decision Date",
        "Distance (miles)", "Council URL",
    ])
    for a in all_applications:
        writer.writerow([
            a.get("reference", ""), a.get("council_name", ""),
            a.get("address", ""), a.get("postcode", ""),
            a.get("description", ""), a.get("application_type", ""),
            a.get("status", ""),
            a.get("submitted_date").isoformat() if a.get("submitted_date") else "",
            a.get("decision_date").isoformat() if a.get("decision_date") else "",
            a.get("distance_miles", ""), a.get("council_url", ""),
        ])
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="planfind_bulk_search.csv"'},
    )


@app.get("/street-history", response_class=HTMLResponse)
async def street_history(request: Request, q: Optional[str] = None):
    applications = []
    q_clean = (q or "").strip()

    if q_clean and len(q_clean) >= 3:
        async with get_db() as db:
            rows = await db.fetch("""
                SELECT
                    a.id, a.reference, a.address, a.postcode, a.description,
                    a.application_type, a.status, a.submitted_date, a.decision_date,
                    a.council_url, c.name AS council_name
                FROM planning_applications a
                JOIN councils c ON c.id = a.council_id
                WHERE a.address ILIKE $1
                ORDER BY a.submitted_date DESC NULLS LAST
                LIMIT 200
            """, f"%{q_clean}%")

        applications = [dict(r) for r in rows]
        for a in applications:
            a["type_badge"] = _type_badge(a.get("application_type", ""))
            a["status_class"] = _status_class(a.get("status", ""))
            a["days_ago"] = _days_ago(a.get("submitted_date"))

        _add_date_availability_flag(applications)

    return render("street_history.html", {
        "request": request,
        "q": q_clean,
        "applications": applications,
        "total": len(applications),
        "searched": bool(q_clean),
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

        app_data = dict(row)

        neighbours = []
        if app_data.get("lat") and app_data.get("lng"):
            nearby = await _fetch_applications(
                db, app_data["lat"], app_data["lng"], radius=0.3, days=365
            )
            neighbours = [n for n in nearby if n["id"] != app_id][:20]

    return render("application.html", {
        "request": request,
        "app": app_data,
        "neighbours": neighbours,
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
        # council_name isn't in the row SELECT above (the whole page is
        # already scoped to one council) — added here just so the shared
        # flag helper below can reuse the same logic everywhere else.
        a["council_name"] = council["name"]

    _add_date_availability_flag(apps)

    return render("council.html", {
        "request": request,
        "council": dict(council),
        "recent": apps,
    })


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return render("about.html", {"request": request})


@app.get("/activity", response_class=HTMLResponse)
async def activity(request: Request):
    async with get_db() as db:
        row = await db.fetchrow("""
            SELECT
                COUNT(*) FILTER (
                    WHERE submitted_date = CURRENT_DATE
                ) AS new_applications,
                COUNT(*) FILTER (
                    WHERE decision_detected_at::date = CURRENT_DATE AND status = 'approved'
                ) AS approved_today,
                COUNT(*) FILTER (
                    WHERE decision_detected_at::date = CURRENT_DATE AND status = 'refused'
                ) AS refused_today,
                COUNT(*) FILTER (
                    WHERE submitted_date = CURRENT_DATE
                    AND application_type ILIKE '%appeal%'
                ) AS appeals_today
            FROM planning_applications
        """)

        recent = await db.fetch("""
            SELECT a.id, a.reference, a.address, a.description,
                   a.application_type, a.status, a.submitted_date,
                   c.name AS council_name, c.slug AS council_slug
            FROM planning_applications a
            JOIN councils c ON c.id = a.council_id
            WHERE a.submitted_date = CURRENT_DATE
            ORDER BY a.id DESC
            LIMIT 10
        """)

    highlights = [dict(r) for r in recent]
    for h in highlights:
        h["type_badge"] = _type_badge(h.get("application_type", ""))
        h["status_class"] = _status_class(h.get("status", ""))

    return render("activity.html", {
        "request": request,
        "today": date.today().strftime("%A, %-d %B %Y"),
        "new_applications": row["new_applications"],
        "approved_today": row["approved_today"],
        "refused_today": row["refused_today"],
        "appeals_today": row["appeals_today"],
        "highlights": highlights,
    })


@app.get("/trends", response_class=HTMLResponse)
async def trends(request: Request):
    async with get_db() as db:
        rows = await db.fetch("""
            SELECT
                c.name,
                c.slug,
                COUNT(*) FILTER (WHERE pa.status IN ('approved', 'refused')) AS decided_count,
                COUNT(*) FILTER (WHERE pa.status = 'approved') AS approved_count,
                COUNT(*) FILTER (WHERE pa.status = 'refused') AS refused_count,
                ROUND(
                    100.0 * COUNT(*) FILTER (WHERE pa.status = 'approved')
                    / NULLIF(COUNT(*) FILTER (WHERE pa.status IN ('approved', 'refused')), 0),
                    1
                ) AS approval_rate_pct
            FROM councils c
            JOIN planning_applications pa ON pa.council_id = c.id
            GROUP BY c.id, c.name, c.slug
            HAVING COUNT(*) FILTER (WHERE pa.status IN ('approved', 'refused')) >= 10
            ORDER BY approval_rate_pct DESC
        """)

    councils_ranked = [dict(r) for r in rows]

    return render("trends.html", {
        "request": request,
        "councils_ranked": councils_ranked,
        "total_councils": len(councils_ranked),
    })


async def _render_tag_page(request: Request, tag: str, status: Optional[str]) -> HTMLResponse:
    meta = TAG_META[tag]
    async with get_db() as db:
        applications = await _fetch_tagged_applications(db, tag, status=status)

    return render("tag_search.html", {
        "request": request,
        "tag": tag,
        "title": meta["title"],
        "intro": meta["intro"],
        "applications": applications,
        "total": len(applications),
        "status": status,
    })


@app.get("/large-sites", response_class=HTMLResponse)
async def large_sites(request: Request, status: Optional[str] = None):
    return await _render_tag_page(request, "large_site", status)


@app.get("/farm-diversification", response_class=HTMLResponse)
async def farm_diversification(request: Request, status: Optional[str] = None):
    return await _render_tag_page(request, "farm_diversification", status)


@app.get("/commercial-conversion", response_class=HTMLResponse)
async def commercial_conversion(request: Request, status: Optional[str] = None):
    return await _render_tag_page(request, "commercial_conversion", status)


@app.get("/councils", response_class=HTMLResponse)
async def councils_list(request: Request):
    async with get_db() as db:
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

    # Converted to plain dicts (asyncpg Records are immutable) so the
    # date-availability note can be added — same confirmed, real
    # limitation as the search-results pages, see
    # COUNCILS_WITHOUT_DATE_DATA above.
    councils = [dict(c) for c in councils]
    for c in councils:
        c["date_unavailable_note"] = c["name"] in COUNCILS_WITHOUT_DATE_DATA

    covered = [
        c for c in councils
        if c["coverage_source"] not in ("pending", "none", "manual_link")
        and c["app_count"] > 0
    ]

    manual_link = [
        c for c in councils
        if c not in covered
        and c["coverage_source"] == "manual_link"
        and c["portal_url"]
    ]

    pending = [c for c in councils if c not in covered and c not in manual_link]

    return render("councils.html", {
        "request": request,
        "covered": covered,
        "manual_link": manual_link,
        "pending": pending,
        "total": len(councils),
        "covered_count": len(covered),
    })


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


def _is_major(app_type: str) -> bool:
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

    # FIX (2026-07-26): this list was missing 'civica_scraper', found via
    # a real, confirmed discrepancy between the homepage stat (134) and
    # the /councils page (135) — St Albans (Civica, real data, 29+
    # applications) was silently excluded from the homepage count and
    # would ALSO have shown "coverage is coming soon" here to any real
    # St Albans resident searching their own postcode, despite genuine
    # live data existing. Listed explicitly (not the exclusion-based
    # approach used in council_count above) since this function doesn't
    # have easy access to re-run that query — kept in sync manually,
    # worth checking here first if a future scraper addition causes the
    # same class of bug again.
    if source in ("idox_scraper", "arcus_scraper", "civica_scraper",
                  "northgate_scraper", "gov_api", "data_gov_uk"):
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
