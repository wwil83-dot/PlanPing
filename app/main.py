"""
PlanPing — FastAPI backend
Run with: uvicorn app.main:app --reload
"""
import os
import csv
import io
import markdown as _markdown_lib
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from jinja2 import Environment, FileSystemLoader

from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_db, lifespan
from app.geocode import postcode_lookup

app = FastAPI(lifespan=lifespan, title="PlanPing")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_jinja = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)


def render(template: str, ctx: dict) -> HTMLResponse:
    return HTMLResponse(_jinja.get_template(template).render(**ctx))


_HOMEPAGE_STATS_CACHE: dict = {"council_count": None, "app_count": None, "cached_at": None}
_HOMEPAGE_STATS_CACHE_TTL = timedelta(minutes=15)


async def _get_cached_homepage_stats(db) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    cached_at = _HOMEPAGE_STATS_CACHE["cached_at"]
    if (cached_at is not None
            and now - cached_at < _HOMEPAGE_STATS_CACHE_TTL):
        return _HOMEPAGE_STATS_CACHE["council_count"], _HOMEPAGE_STATS_CACHE["app_count"]

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
    _HOMEPAGE_STATS_CACHE["council_count"] = council_count
    _HOMEPAGE_STATS_CACHE["app_count"] = app_count
    _HOMEPAGE_STATS_CACHE["cached_at"] = now
    return council_count, app_count


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    async with get_db() as db:
        council_count, app_count = await _get_cached_homepage_stats(db)
    return render("index.html", {
        "request": request,
        "council_count": council_count,
        "app_count": app_count,
    })


@app.get("/postcode-search", response_class=HTMLResponse)
async def postcode_search_page(request: Request):
    return render("postcode_search.html", {"request": request})


def _normalize_keyword(keyword: Optional[str]) -> Optional[str]:
    keyword = keyword.strip() if keyword else ""
    return keyword or None


def _parse_date_param(value: Optional[str]) -> Optional[date]:
    value = value.strip() if value else ""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


SORT_OPTIONS = {
    "date_desc": "a.submitted_date DESC NULLS LAST, an.distance_miles",
    "date_asc": "a.submitted_date ASC NULLS LAST, an.distance_miles",
    "distance": "an.distance_miles, a.submitted_date DESC NULLS LAST",
}
DEFAULT_SORT = "date_desc"

TAG_SORT_OPTIONS = {
    "date_desc": "a.submitted_date DESC NULLS LAST",
    "date_asc": "a.submitted_date ASC NULLS LAST",
}


def _resolve_sort_order(sort: Optional[str]) -> str:
    return SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])


def _widen_days_for_date_range(days: int, date_from: Optional[date]) -> int:
    if date_from is None:
        return days
    widened = (date.today() - date_from).days
    return max(days, widened)


async def _fetch_applications(db, lat: float, lng: float, radius: float, days: int,
                               status: Optional[str] = None,
                               app_type: Optional[str] = None,
                               keyword: Optional[str] = None,
                               sort: Optional[str] = None,
                               date_from: Optional[date] = None,
                               date_to: Optional[date] = None) -> list[dict]:
    status = status or None
    app_type = app_type or None
    keyword = _normalize_keyword(keyword)

    effective_days = _widen_days_for_date_range(days, date_from)
    order_by = _resolve_sort_order(sort)

    rows = await db.fetch(f"""
        SELECT
            a.id, a.reference, a.address, a.postcode,
            a.description, a.application_type, a.status,
            a.submitted_date, a.decision_date, a.council_url,
            a.lat, a.lng, a.geocode_quality,
            c.name AS council_name, c.slug AS council_slug,
            c.coverage_source,
            an.distance_miles
        FROM applications_near($1, $2, $3, $4) an
        JOIN planning_applications a ON a.id = an.application_id
        JOIN councils c ON c.id = a.council_id
        WHERE ($5::text IS NULL OR a.status = $5)
        AND ($6::text IS NULL OR a.description ILIKE '%' || $6 || '%'
                              OR a.address ILIKE '%' || $6 || '%')
        AND ($7::date IS NULL OR a.submitted_date >= $7)
        AND ($8::date IS NULL OR a.submitted_date <= $8)
        ORDER BY {order_by}
    """, lat, lng, radius, effective_days, status, keyword, date_from, date_to)

    applications = [dict(r) for r in rows]
    for a in applications:
        a["distance_miles"] = round(a["distance_miles"], 1)
        a["type_badge"] = _type_badge(a.get("application_type", ""), a.get("reference", ""))
        a["is_major"] = _is_major(a.get("application_type", ""), a.get("reference", ""))
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
                                      council_slug: Optional[str] = None,
                                      keyword: Optional[str] = None,
                                      sort: Optional[str] = None,
                                      date_from: Optional[date] = None,
                                      date_to: Optional[date] = None,
                                      limit: int = 200) -> list[dict]:
    keyword = _normalize_keyword(keyword)
    order_by = TAG_SORT_OPTIONS.get(sort, TAG_SORT_OPTIONS["date_desc"])

    rows = await db.fetch(f"""
        SELECT
            a.id, a.reference, a.address, a.postcode,
            a.description, a.application_type, a.status,
            a.submitted_date, a.decision_date, a.council_url,
            c.name AS council_name, c.slug AS council_slug
        FROM planning_applications a
        JOIN councils c ON c.id = a.council_id
        WHERE a.tags @> ARRAY[$1]::text[]
        AND ($2::text IS NULL OR a.status = $2)
        AND ($3::text IS NULL OR c.slug = $3)
        AND ($4::text IS NULL OR a.description ILIKE '%' || $4 || '%'
                              OR a.address ILIKE '%' || $4 || '%')
        AND ($5::date IS NULL OR a.submitted_date >= $5)
        AND ($6::date IS NULL OR a.submitted_date <= $6)
        ORDER BY {order_by}
        LIMIT $7
    """, tag, status, council_slug, keyword, date_from, date_to, limit)

    applications = [dict(r) for r in rows]
    for a in applications:
        a["type_badge"] = _type_badge(a.get("application_type", ""), a.get("reference", ""))
        a["is_major"] = _is_major(a.get("application_type", ""), a.get("reference", ""))
        a["status_class"] = _status_class(a.get("status", ""))
        a["days_ago"] = _days_ago(a.get("submitted_date"))

    _add_date_availability_flag(applications)
    return applications


async def _fetch_tag_council_options(db, tag: str) -> list[dict]:
    rows = await db.fetch("""
        SELECT DISTINCT c.name, c.slug
        FROM planning_applications a
        JOIN councils c ON c.id = a.council_id
        WHERE a.tags @> ARRAY[$1]::text[]
        ORDER BY c.name
    """, tag)
    return [dict(r) for r in rows]


STATUS_FILTER_OPTIONS = ["pending", "approved", "refused", "withdrawn"]
TYPE_FILTER_OPTIONS = ["householder", "full", "outline", "listed", "tree",
                       "advert", "prior", "major", "other"]

COUNCILS_WITHOUT_DATE_DATA = {
    "Powys County Council",
    "Erewash Borough Council",
    "Reading Borough Council",
    "Wrexham County Borough Council",
}


def _add_date_availability_flag(applications: list[dict]) -> None:
    for a in applications:
        a["date_unavailable_note"] = (
            a.get("submitted_date") is None
            and a.get("council_name") in COUNCILS_WITHOUT_DATE_DATA
        )



@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, postcode: str, radius: float = 1.0, days: int = 30,
                  status: Optional[str] = None, app_type: Optional[str] = None,
                  keyword: Optional[str] = None, sort: Optional[str] = None,
                  date_from: Optional[str] = None, date_to: Optional[str] = None):
    postcode = postcode.strip().upper()
    location = await postcode_lookup(postcode)
    date_from_parsed = _parse_date_param(date_from)
    date_to_parsed = _parse_date_param(date_to)

    if not location:
        async with get_db() as db:
            council_count, app_count = await _get_cached_homepage_stats(db)
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
        applications = await _fetch_applications(
            db, lat, lng, radius, days, status, app_type, keyword,
            sort, date_from_parsed, date_to_parsed,
        )

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
        "keyword": keyword or "",
        "sort": sort or DEFAULT_SORT,
        "sort_options": SORT_OPTIONS,
        "date_from": date_from_parsed.isoformat() if date_from_parsed else "",
        "date_to": date_to_parsed.isoformat() if date_to_parsed else "",
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
                      status: Optional[str] = None, app_type: Optional[str] = None,
                      keyword: Optional[str] = None, sort: Optional[str] = None,
                      date_from: Optional[str] = None, date_to: Optional[str] = None):
    postcode = postcode.strip().upper()
    location = await postcode_lookup(postcode)
    if not location:
        raise HTTPException(status_code=404, detail=f"Could not find postcode '{postcode}'")

    lat, lng = location["lat"], location["lng"]

    async with get_db() as db:
        applications = await _fetch_applications(
            db, lat, lng, radius, days, status, app_type, keyword,
            sort, _parse_date_param(date_from), _parse_date_param(date_to),
        )

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
            a["type_badge"] = _type_badge(a.get("application_type", ""), a.get("reference", ""))
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
        _add_date_availability_flag([app_data])

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
                   lat, lng, geocode_quality
            FROM planning_applications
            WHERE council_id = $1
            ORDER BY submitted_date DESC NULLS LAST
            LIMIT 50
        """, council["id"])

    apps = [dict(r) for r in recent]
    for a in apps:
        a["type_badge"] = _type_badge(a.get("application_type", ""), a.get("reference", ""))
        a["is_major"] = _is_major(a.get("application_type", ""), a.get("reference", ""))
        a["is_mapped"] = a.get("lat") is not None
        a["days_ago"] = _days_ago(a.get("submitted_date"))
        a["council_name"] = council["name"]

    _add_date_availability_flag(apps)

    # ADDED (2026-09-16) — real, direct report: this page's "Approx."
    # badge had no accompanying map at all, unlike the postcode search
    # page which already shows exactly this kind of marker distinction.
    # Same map_markers shape as /search and /towns/{slug}, built from
    # the same real coordinates already selected above — only
    # applications with a genuine lat/lng get a marker for now.
    map_markers = [
        {
            "id": a["id"],
            "lat": a["lat"],
            "lng": a["lng"],
            "reference": a.get("reference") or "",
            "address": a.get("address") or "",
            "is_centroid": a.get("geocode_quality") == "centroid",
        }
        for a in apps
        if a.get("lat") is not None and a.get("lng") is not None
    ]

    council_dict = dict(council)
    council_dict["date_unavailable_note"] = council["name"] in COUNCILS_WITHOUT_DATE_DATA

    fallback_date = apps[0].get("submitted_date") if apps else None
    days_since_save = _effective_days_since_save(council["last_saved_at"], fallback_date)
    council_dict["days_since_save"] = days_since_save
    council_dict["status"] = _coverage_status(council["coverage_source"], days_since_save)

    return render("council.html", {
        "request": request,
        "council": council_dict,
        "recent": apps,
        "map_markers": map_markers,
        "lat": apps[0]["lat"] if apps and apps[0].get("lat") is not None else None,
        "lng": apps[0]["lng"] if apps and apps[0].get("lat") is not None else None,
    })


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    async with get_db() as db:
        council_count, app_count = await _get_cached_homepage_stats(db)
    return render("about.html", {
        "request": request,
        "council_count": council_count,
        "app_count": app_count,
    })


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
        h["type_badge"] = _type_badge(h.get("application_type", ""), h.get("reference", ""))
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
        approval_rows = await db.fetch("""
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

        most_active_rows = await db.fetch("""
            SELECT c.name, c.slug, COUNT(*) AS recent_count
            FROM planning_applications pa
            JOIN councils c ON c.id = pa.council_id
            WHERE pa.submitted_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY c.id, c.name, c.slug
            ORDER BY recent_count DESC
            LIMIT 10
        """)

        niche_leaders = {}
        for tag_key in ("farm_diversification", "commercial_conversion", "large_site"):
            rows = await db.fetch("""
                SELECT
                    c.name, c.slug,
                    COUNT(*) FILTER (WHERE pa.tags @> ARRAY[$1]::text[]) AS tag_count,
                    COUNT(*) AS total_count,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE pa.tags @> ARRAY[$1]::text[])
                        / NULLIF(COUNT(*), 0),
                        1
                    ) AS tag_pct
                FROM planning_applications pa
                JOIN councils c ON c.id = pa.council_id
                WHERE pa.submitted_date >= CURRENT_DATE - INTERVAL '90 days'
                GROUP BY c.id, c.name, c.slug
                HAVING COUNT(*) FILTER (WHERE pa.tags @> ARRAY[$1]::text[]) >= 3
                ORDER BY tag_pct DESC
                LIMIT 5
            """, tag_key)
            niche_leaders[tag_key] = [dict(r) for r in rows]

        type_rows = await db.fetch("""
            SELECT application_type, reference
            FROM planning_applications
            WHERE submitted_date >= CURRENT_DATE - INTERVAL '90 days'
        """)
        type_counts: dict[str, int] = {}
        for r in type_rows:
            badge = _type_badge(r["application_type"] or "", r["reference"] or "")
            type_counts[badge] = type_counts.get(badge, 0) + 1
        total_typed = sum(type_counts.values())
        type_mix = sorted(
            [
                {"badge": k, "count": v, "pct": round(100 * v / total_typed, 1) if total_typed else 0}
                for k, v in type_counts.items()
            ],
            key=lambda x: x["count"], reverse=True,
        )

        growth_rows = await db.fetch("""
            SELECT
                c.name, c.slug,
                COUNT(*) FILTER (WHERE pa.submitted_date >= CURRENT_DATE - INTERVAL '30 days') AS recent_count,
                COUNT(*) FILTER (WHERE pa.submitted_date >= CURRENT_DATE - INTERVAL '60 days'
                                  AND pa.submitted_date < CURRENT_DATE - INTERVAL '30 days') AS prior_count
            FROM planning_applications pa
            JOIN councils c ON c.id = pa.council_id
            WHERE pa.submitted_date >= CURRENT_DATE - INTERVAL '60 days'
            GROUP BY c.id, c.name, c.slug
            HAVING COUNT(*) FILTER (WHERE pa.submitted_date >= CURRENT_DATE - INTERVAL '60 days'
                                      AND pa.submitted_date < CURRENT_DATE - INTERVAL '30 days') >= 5
        """)
        growth_list = []
        for r in growth_rows:
            pct_change = round(100 * (r["recent_count"] - r["prior_count"]) / r["prior_count"], 1)
            growth_list.append({
                "name": r["name"], "slug": r["slug"],
                "recent_count": r["recent_count"], "prior_count": r["prior_count"],
                "pct_change": pct_change,
            })
        growth_list.sort(key=lambda x: x["pct_change"], reverse=True)
        growth_top = growth_list[:10]

    councils_ranked = [dict(r) for r in approval_rows]

    return render("trends.html", {
        "request": request,
        "councils_ranked": councils_ranked,
        "total_councils": len(councils_ranked),
        "most_active": [dict(r) for r in most_active_rows],
        "niche_leaders": niche_leaders,
        "niche_labels": {
            "farm_diversification": "Farm Diversification",
            "commercial_conversion": "Commercial Conversion",
            "large_site": "Large Sites",
        },
        "type_mix": type_mix,
        "growth_top": growth_top,
    })


async def _render_tag_page(request: Request, tag: str, status: Optional[str],
                            council: Optional[str], keyword: Optional[str] = None,
                            sort: Optional[str] = None,
                            date_from: Optional[str] = None,
                            date_to: Optional[str] = None) -> HTMLResponse:
    status = status or None
    council = council or None
    date_from_parsed = _parse_date_param(date_from)
    date_to_parsed = _parse_date_param(date_to)

    meta = TAG_META[tag]
    async with get_db() as db:
        applications = await _fetch_tagged_applications(
            db, tag, status=status, council_slug=council,
            keyword=keyword, sort=sort,
            date_from=date_from_parsed, date_to=date_to_parsed,
        )
        council_options = await _fetch_tag_council_options(db, tag)

    return render("tag_search.html", {
        "request": request,
        "tag": tag,
        "title": meta["title"],
        "intro": meta["intro"],
        "applications": applications,
        "total": len(applications),
        "status": status,
        "council": council,
        "keyword": keyword or "",
        "sort": sort or DEFAULT_SORT,
        "date_from": date_from_parsed.isoformat() if date_from_parsed else "",
        "date_to": date_to_parsed.isoformat() if date_to_parsed else "",
        "council_options": council_options,
    })


@app.get("/large-sites", response_class=HTMLResponse)
async def large_sites(request: Request, status: Optional[str] = None, council: Optional[str] = None,
                       keyword: Optional[str] = None, sort: Optional[str] = None,
                       date_from: Optional[str] = None, date_to: Optional[str] = None):
    return await _render_tag_page(request, "large_site", status, council, keyword, sort, date_from, date_to)


@app.get("/farm-diversification", response_class=HTMLResponse)
async def farm_diversification(request: Request, status: Optional[str] = None, council: Optional[str] = None,
                                keyword: Optional[str] = None, sort: Optional[str] = None,
                                date_from: Optional[str] = None, date_to: Optional[str] = None):
    return await _render_tag_page(request, "farm_diversification", status, council, keyword, sort, date_from, date_to)


@app.get("/commercial-conversion", response_class=HTMLResponse)
async def commercial_conversion(request: Request, status: Optional[str] = None, council: Optional[str] = None,
                                 keyword: Optional[str] = None, sort: Optional[str] = None,
                                 date_from: Optional[str] = None, date_to: Optional[str] = None):
    return await _render_tag_page(request, "commercial_conversion", status, council, keyword, sort, date_from, date_to)


_COUNCILS_WITH_COUNTS_CACHE: dict = {"rows": None, "cached_at": None}
_COUNCILS_WITH_COUNTS_CACHE_TTL = timedelta(minutes=15)


async def _get_cached_councils_with_counts(db) -> list[dict]:
    now = datetime.now(timezone.utc)
    cached_at = _COUNCILS_WITH_COUNTS_CACHE["cached_at"]
    if (_COUNCILS_WITH_COUNTS_CACHE["rows"] is not None
            and cached_at is not None
            and now - cached_at < _COUNCILS_WITH_COUNTS_CACHE_TTL):
        return _COUNCILS_WITH_COUNTS_CACHE["rows"]

    rows = await db.fetch("""
        SELECT c.name, c.slug, c.region, c.system, c.coverage_source, c.portal_url,
               c.last_saved_at,
               COUNT(pa.id) AS app_count,
               MAX(pa.submitted_date) AS latest_date
        FROM councils c
        LEFT JOIN planning_applications pa ON pa.council_id = c.id
        WHERE c.active = TRUE
        GROUP BY c.id, c.name, c.slug, c.region, c.system, c.coverage_source,
                 c.portal_url, c.last_saved_at
        ORDER BY c.name
    """)
    result = [dict(r) for r in rows]
    _COUNCILS_WITH_COUNTS_CACHE["rows"] = result
    _COUNCILS_WITH_COUNTS_CACHE["cached_at"] = now
    return result


@app.get("/councils", response_class=HTMLResponse)
async def councils_list(request: Request):
    async with get_db() as db:
        councils = await _get_cached_councils_with_counts(db)

    councils = [dict(c) for c in councils]
    for c in councils:
        c["date_unavailable_note"] = c["name"] in COUNCILS_WITHOUT_DATE_DATA

    covered = [
        c for c in councils
        if c["coverage_source"] not in ("pending", "none", "manual_link")
        and c["app_count"] > 0
    ]
    for c in covered:
        c["area_aliases"] = COUNCIL_AREA_ALIASES.get(c["name"], [])
        c["search_haystack"] = " ".join([c["name"]] + c["area_aliases"]).lower()
        days_since_save = _effective_days_since_save(c["last_saved_at"], c["latest_date"])
        c["days_since_save"] = days_since_save
        c["status"] = _coverage_status(c["coverage_source"], days_since_save)

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


@app.get("/api/coverage-map-data")
async def coverage_map_data():
    async with get_db() as db:
        councils = await _get_cached_councils_with_counts(db)

    result = []
    for c in councils:
        if c["coverage_source"] in ("pending", "none", "manual_link") or c["app_count"] == 0:
            continue
        days_since_save = _effective_days_since_save(c["last_saved_at"], c["latest_date"])
        status = _coverage_status(c["coverage_source"], days_since_save)
        result.append({
            "name": c["name"],
            "status": status["key"],
            "app_count": c["app_count"],
        })

    return JSONResponse(result)


KNOWN_GAP_REASONS = {
    "Solihull Metropolitan Borough Council":
        "The council's server is refusing connections from our automated "
        "systems specifically (confirmed consistent, not a general outage).",
    "Bolsover District Council":
        "The council's website is blocking automated access with a security "
        "check (confirmed via a real form-submission test).",
    "North East Derbyshire District Council":
        "The council's website is blocking automated access with a security "
        "check (confirmed via a real form-submission test).",
    "Brighton and Hove City Council":
        "The council's planning search consistently returns a blank page to "
        "our automated systems (confirmed via repeated, independent tests).",
}

GAP_THRESHOLD_DAYS = 10

DELAYED_THRESHOLD_DAYS = 3


def _effective_days_since_save(last_saved_at, fallback_date: Optional[date]) -> Optional[int]:
    if last_saved_at is not None:
        return (date.today() - last_saved_at.date()).days
    if fallback_date is not None:
        return (date.today() - fallback_date).days
    return None


def _coverage_status(coverage_source: str, days_since_save: Optional[int]) -> dict:
    if coverage_source in ("pending", "none", "manual_link"):
        return {"key": "offline", "emoji": "🔴", "label": "Not yet covered"}
    if days_since_save is None:
        return {"key": "offline", "emoji": "🔴", "label": "Offline"}
    if days_since_save >= GAP_THRESHOLD_DAYS:
        return {"key": "offline", "emoji": "🔴", "label": "Offline"}
    if days_since_save >= DELAYED_THRESHOLD_DAYS:
        return {"key": "delayed", "emoji": "🟠", "label": "Delayed"}
    return {"key": "live", "emoji": "🟢", "label": "Live"}


COUNCIL_AREA_ALIASES = {
    "North Yorkshire Council": [
        "Harrogate", "Scarborough", "Craven", "Hambleton", "Selby",
    ],
}



@app.get("/coverage-gaps", response_class=HTMLResponse)
async def coverage_gaps(request: Request):
    async with get_db() as db:
        rows = await db.fetch("""
            SELECT name, slug, system, coverage_source, portal_url,
                   last_saved_at,
                   (CURRENT_DATE - last_saved_at::date) AS days_since_save
            FROM councils
            WHERE active = true
            AND coverage_source NOT IN ('pending', 'none', 'manual_link')
            AND last_saved_at IS NOT NULL
            AND last_saved_at < NOW() - (INTERVAL '1 day' * $1)
            ORDER BY last_saved_at ASC
        """, GAP_THRESHOLD_DAYS)

    gaps = [dict(r) for r in rows]
    for g in gaps:
        g["known_reason"] = KNOWN_GAP_REASONS.get(g["name"])

    return render("coverage_gaps.html", {
        "request": request,
        "gaps": gaps,
        "total": len(gaps),
        "threshold_days": GAP_THRESHOLD_DAYS,
    })


TOWN_RADIUS_MILES = 3.0
TOWN_DAYS_BACK = 30


@app.get("/towns", response_class=HTMLResponse)
async def towns_index(request: Request, q: Optional[str] = None):
    q_clean = (q or "").strip()

    async with get_db() as db:
        if q_clean and len(q_clean) >= 2:
            rows = await db.fetch("""
                SELECT name, slug, county, region
                FROM towns
                WHERE name ILIKE '%' || $1 || '%'
                ORDER BY similarity(name, $1) DESC, name
                LIMIT 50
            """, q_clean)
        else:
            rows = await db.fetch("""
                SELECT name, slug, county, region
                FROM towns
                ORDER BY name
                LIMIT 100
            """)

        total_towns = await db.fetchval("SELECT COUNT(*) FROM towns")
        counties = await db.fetch("""
            SELECT DISTINCT county FROM towns
            WHERE county IS NOT NULL AND county != ''
            ORDER BY county
        """)

    return render("towns.html", {
        "request": request,
        "q": q_clean,
        "towns": [dict(r) for r in rows],
        "total_towns": total_towns,
        "counties": [c["county"] for c in counties],
        "searched": bool(q_clean),
    })


@app.get("/towns/county/{county_slug}", response_class=HTMLResponse)
async def towns_by_county(request: Request, county_slug: str):
    county_name = county_slug.replace("-", " ")

    async with get_db() as db:
        rows = await db.fetch("""
            SELECT name, slug, county, region
            FROM towns
            WHERE county ILIKE $1
            ORDER BY name
        """, county_name)

    if not rows:
        raise HTTPException(404, "County not found")

    return render("towns.html", {
        "request": request,
        "q": "",
        "towns": [dict(r) for r in rows],
        "total_towns": len(rows),
        "counties": [],
        "searched": False,
        "county_filter": rows[0]["county"],
    })


@app.get("/towns/{slug}", response_class=HTMLResponse)
async def town_page(request: Request, slug: str, radius: float = TOWN_RADIUS_MILES,
                     days: int = TOWN_DAYS_BACK, status: Optional[str] = None,
                     app_type: Optional[str] = None, keyword: Optional[str] = None,
                     sort: Optional[str] = None, date_from: Optional[str] = None,
                     date_to: Optional[str] = None):
    date_from_parsed = _parse_date_param(date_from)
    date_to_parsed = _parse_date_param(date_to)

    async with get_db() as db:
        town = await db.fetchrow("SELECT * FROM towns WHERE slug = $1", slug)
        if not town:
            raise HTTPException(404, "Town not found")

        applications = await _fetch_applications(
            db, town["lat"], town["lng"], radius, days, status, app_type,
            keyword, sort, date_from_parsed, date_to_parsed,
        )

        council = None
        if town["county"]:
            council = await db.fetchrow("""
                SELECT id, name, slug, coverage_source, portal_url, system
                FROM councils
                WHERE name ILIKE $1
                LIMIT 1
            """, f"%{town['county']}%")

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

    coverage = _coverage_message(council, town["county"] or "") if council else None

    return render("town.html", {
        "request": request,
        "town": dict(town),
        "radius": radius,
        "days": days,
        "status": status,
        "app_type": app_type,
        "keyword": keyword or "",
        "sort": sort or DEFAULT_SORT,
        "sort_options": SORT_OPTIONS,
        "date_from": date_from_parsed.isoformat() if date_from_parsed else "",
        "date_to": date_to_parsed.isoformat() if date_to_parsed else "",
        "status_options": STATUS_FILTER_OPTIONS,
        "type_options": TYPE_FILTER_OPTIONS,
        "applications": applications,
        "map_markers": map_markers,
        "total": len(applications),
        "lat": town["lat"],
        "lng": town["lng"],
        "council": dict(council) if council else None,
        "coverage": coverage,
    })


GUIDE_CATEGORY_META = {
    "getting_started": {
        "title": "Getting Started",
        "description": "The basics of applying for planning permission and how the process works.",
    },
    "farm_diversification": {
        "title": "Farm Diversification",
        "description": "Converting agricultural buildings under permitted development.",
    },
    "commercial_conversion": {
        "title": "Commercial Conversion",
        "description": "Converting commercial buildings to residential use.",
    },
    "large_sites": {
        "title": "Large Sites",
        "description": "Major applications, large developments, and how they differ.",
    },
}

GUIDE_CATEGORY_TAG = {
    "farm_diversification": "farm_diversification",
    "commercial_conversion": "commercial_conversion",
    "large_sites": "large_site",
}
GUIDE_CATEGORY_SEARCH_URL = {
    "farm_diversification": "/farm-diversification",
    "commercial_conversion": "/commercial-conversion",
    "large_sites": "/large-sites",
}

GUIDE_CATEGORY_NATIONAL_STAT = {
    "farm_diversification": {
        "number": "462",
        "label": "new homes from agricultural building conversions in England",
        "period": "2024–25",
        "source": "MHCLG, Housing Supply: Net Additional Dwellings",
    },
    "commercial_conversion": {
        "number": "6,202",
        "label": "new homes from office and commercial building conversions in England",
        "period": "2024–25",
        "source": "MHCLG, Housing Supply: Net Additional Dwellings",
    },
    "large_sites": {
        "number": "206,000",
        "label": "housing units applied for via outline applications in England",
        "period": "year to Dec 2025",
        "source": "MHCLG Planning Applications Statistics",
    },
}


@app.get("/guides", response_class=HTMLResponse)
async def guides_index(request: Request):
    async with get_db() as db:
        rows = await db.fetch("""
            SELECT slug, category, title, summary, reading_minutes
            FROM guides
            ORDER BY category, title
        """)

        category_counts: dict[str, int] = {}
        for cat_key, tag in GUIDE_CATEGORY_TAG.items():
            count = await db.fetchval(
                "SELECT COUNT(*) FROM planning_applications WHERE tags @> ARRAY[$1]::text[]",
                tag,
            )
            category_counts[cat_key] = count

    guides_by_category: dict[str, list[dict]] = {}
    for r in rows:
        guides_by_category.setdefault(r["category"], []).append(dict(r))

    return render("guides.html", {
        "request": request,
        "guides_by_category": guides_by_category,
        "category_meta": GUIDE_CATEGORY_META,
        "category_counts": category_counts,
        "category_search_url": GUIDE_CATEGORY_SEARCH_URL,
        "category_national_stat": GUIDE_CATEGORY_NATIONAL_STAT,
    })


@app.get("/guides/{slug}", response_class=HTMLResponse)
async def guide_detail(request: Request, slug: str):
    async with get_db() as db:
        guide = await db.fetchrow("SELECT * FROM guides WHERE slug = $1", slug)
        if not guide:
            raise HTTPException(404, "Guide not found")

        related = await db.fetch("""
            SELECT slug, title, summary
            FROM guides
            WHERE category = $1 AND slug != $2
            ORDER BY title
            LIMIT 3
        """, guide["category"], slug)

    body_html = _markdown_lib.markdown(guide["body_markdown"])

    return render("guide.html", {
        "request": request,
        "guide": dict(guide),
        "body_html": body_html,
        "category_meta": GUIDE_CATEGORY_META.get(guide["category"], {}),
        "related": [dict(r) for r in related],
    })


PROFESSIONAL_TRADE_LABELS = {
    "architect":        "Architects",
    "general_builder":  "General Builders",
    "electrician":      "Electricians",
    "plumber_heating":  "Plumbers & Heating Engineers",
    "roofer":           "Roofers",
    "plasterer":        "Plasterers",
    "joiner_carpenter": "Joiners & Carpenters",
    "glazier":          "Glaziers",
}

_PROFESSIONALS_STATIC_CACHE: dict = {"names": None, "last_synced": None, "cached_at": None}
_PROFESSIONALS_STATIC_CACHE_TTL = timedelta(hours=6)


async def _get_cached_professionals_static_data(db) -> tuple[list[str], object]:
    now = datetime.now(timezone.utc)
    cached_at = _PROFESSIONALS_STATIC_CACHE["cached_at"]
    if (_PROFESSIONALS_STATIC_CACHE["names"] is not None
            and cached_at is not None
            and now - cached_at < _PROFESSIONALS_STATIC_CACHE_TTL):
        return _PROFESSIONALS_STATIC_CACHE["names"], _PROFESSIONALS_STATIC_CACHE["last_synced"]

    rows = await db.fetch("""
        SELECT DISTINCT t.name
        FROM professionals p
        JOIN towns t ON t.id = p.town_id
        ORDER BY t.name
    """)
    names = [r["name"] for r in rows]
    last_synced = await db.fetchval("SELECT MAX(last_synced_at) FROM professionals")

    _PROFESSIONALS_STATIC_CACHE["names"] = names
    _PROFESSIONALS_STATIC_CACHE["last_synced"] = last_synced
    _PROFESSIONALS_STATIC_CACHE["cached_at"] = now
    return names, last_synced


@app.get("/find-a-professional", response_class=HTMLResponse)
async def find_a_professional(request: Request, trade: Optional[str] = None, town: Optional[str] = None,
                                keyword: Optional[str] = None, page: int = 1):
    trade = trade or None
    town = (town or "").strip() or None
    keyword = _normalize_keyword(keyword)
    has_filter = bool(trade or town or keyword)

    PAGE_SIZE = 50
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    professionals = []
    total_count = 0

    if has_filter:
        async with get_db() as db:
            total_count = await db.fetchval("""
                SELECT COUNT(*)
                FROM professionals p
                JOIN towns t ON t.id = p.town_id
                WHERE ($1::text IS NULL OR p.trade_category = $1)
                AND ($2::text IS NULL OR t.name ILIKE '%' || $2 || '%')
                AND ($3::text IS NULL OR p.company_name ILIKE '%' || $3 || '%')
            """, trade, town, keyword)

            professionals = await db.fetch("""
                SELECT p.id, p.company_name, p.trade_category, p.postcode,
                       p.address, p.lat, p.lng, p.incorporated_date,
                       t.name AS town_name, t.slug AS town_slug, t.county
                FROM professionals p
                JOIN towns t ON t.id = p.town_id
                WHERE ($1::text IS NULL OR p.trade_category = $1)
                AND ($2::text IS NULL OR t.name ILIKE '%' || $2 || '%')
                AND ($3::text IS NULL OR p.company_name ILIKE '%' || $3 || '%')
                ORDER BY t.name, p.trade_category, p.company_name
                LIMIT $4 OFFSET $5
            """, trade, town, keyword, PAGE_SIZE, offset)

    async with get_db() as db:
        town_names, last_synced = await _get_cached_professionals_static_data(db)

    professionals = [dict(r) for r in professionals]
    for p in professionals:
        p["trade_label"] = PROFESSIONAL_TRADE_LABELS.get(p["trade_category"], p["trade_category"])

    map_markers = [
        {
            "id": p["id"],
            "lat": p["lat"],
            "lng": p["lng"],
            "company_name": p["company_name"],
            "trade_label": p["trade_label"],
            "town_name": p["town_name"],
        }
        for p in professionals
        if p.get("lat") is not None and p.get("lng") is not None
    ]

    return render("find_a_professional.html", {
        "request": request,
        "professionals": professionals,
        "total": len(professionals),
        "map_markers": map_markers,
        "trade": trade,
        "town": town,
        "keyword": keyword or "",
        "trade_options": PROFESSIONAL_TRADE_LABELS,
        "town_names": town_names,
        "last_synced": last_synced,
        "has_filter": has_filter,
        "page": page,
        "total_count": total_count,
        "total_pages": max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE),
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


def _is_outline_reference(reference: str) -> bool:
    if not reference:
        return False
    last_segment = reference.strip().split("/")[-1].upper()
    return last_segment.startswith("OUT")


def _is_major(app_type: str, reference: str = "") -> bool:
    t = (app_type or "").upper()
    major_keywords = ["OUTLINE", "OUT", "MAJOR", "EIA", "HYBRID",
                      "PERMISSION IN PRINCIPLE", "PIP", "TECHNICAL DETAILS"]
    if any(k in t for k in major_keywords):
        return True
    return _is_outline_reference(reference)


def _type_badge(app_type: str, reference: str = "") -> str:
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
    if _is_outline_reference(reference):
        return "outline"
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
