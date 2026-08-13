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


def _normalize_keyword(keyword: Optional[str]) -> Optional[str]:
    """Empty or whitespace-only input becomes None (no filter) — same
    lesson already learned once this session for status/app_type: a
    blank field submitted alongside real filters must never silently
    exclude every result. Pulled out as its own function so tests
    exercise the real logic, not a separate copy that could drift."""
    keyword = keyword.strip() if keyword else ""
    return keyword or None


def _parse_date_param(value: Optional[str]) -> Optional[date]:
    """BUG FIX (2026-08-13) — a real, confirmed production bug. Route
    parameters typed as `Optional[date]` get validated by FastAPI/
    Pydantic BEFORE our own code ever runs — an empty string fails that
    validation as "not a valid date" and returns a 422, since Pydantic
    treats a blank string as malformed input, not as an absent
    parameter. This is the exact same empty-string-vs-None lesson
    already learned and fixed for status/app_type/keyword — but those
    are plain `Optional[str]` params, where FastAPI passes the empty
    string straight through and OUR OWN code gets to normalize it. A
    strictly-typed `Optional[date]` never gives us that chance at all.
    The real fix: routes accept the raw string (Optional[str]) and call
    this function to parse it themselves — normalizing blank/whitespace
    input to None first, exactly like every other filter on this site,
    THEN parsing whatever's left into a real date. A genuinely malformed
    date (not just blank) is treated as "no filter" too, rather than
    crashing the whole page over one bad value in the URL."""
    value = value.strip() if value else ""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ADDED (2026-08-11) — allowlisted sort options. SQL ORDER BY can't be
# parameterized with $N placeholders the way column VALUES can — the
# only safe way to make it user-selectable is to map a validated key
# to a fixed, hardcoded SQL snippet like this, never interpolate raw
# user input into the query string directly.
SORT_OPTIONS = {
    "date_desc": "a.submitted_date DESC NULLS LAST, an.distance_miles",
    "date_asc": "a.submitted_date ASC NULLS LAST, an.distance_miles",
    "distance": "an.distance_miles, a.submitted_date DESC NULLS LAST",
}
DEFAULT_SORT = "date_desc"

# BUG FIX (2026-08-11) — a genuinely SEPARATE mapping for tag pages,
# after SORT_OPTIONS above caused a 500 on every single tag-page
# request regardless of which sort was chosen. Both "date_desc" and
# "date_asc" reference "an.distance_miles" as a secondary tiebreaker —
# a table alias from the applications_near() Postgres function used by
# the main postcode search, which tag pages never join against at all
# (no postcode/search point on those pages for a distance to be
# relative to). Deliberately has no "distance" key at all — there's
# nothing correct it could ever map to here.
TAG_SORT_OPTIONS = {
    "date_desc": "a.submitted_date DESC NULLS LAST",
    "date_asc": "a.submitted_date ASC NULLS LAST",
}


def _resolve_sort_order(sort: Optional[str]) -> str:
    """Maps a validated sort key to its fixed SQL snippet, defaulting to
    DEFAULT_SORT for anything unrecognized (including None) — this is
    the actual safety boundary: an unknown/malicious value never
    reaches the query string, it just falls back to the default."""
    return SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])


def _widen_days_for_date_range(days: int, date_from: Optional[date]) -> int:
    """applications_near() only accepts a simple lookback window, not an
    explicit date range — when a real date_from is given, this widens
    the window passed into that function so its own internal filter
    can't accidentally exclude something the precise date_from/date_to
    check (applied separately, directly on a.submitted_date) actually
    wants. Never narrows — only ever returns days itself, or something
    larger."""
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
    # FIX (2026-07-30) — same real bug found and fixed on the tag pages,
    # applied here at the shared source so every caller (search,
    # search_csv, bulk_search, application_detail's neighbours) is
    # covered by one change rather than patching each route separately.
    # A GET form with multiple dropdowns in one <form> submits every
    # field, including untouched ones left at their blank default
    # ("" from <option value="">Any status</option>") — an empty string
    # isn't the same as an absent parameter, and the old SQL check only
    # treated a genuinely missing value as "no filter".
    status = status or None
    app_type = app_type or None
    keyword = _normalize_keyword(keyword)

    # ADDED (2026-08-11) — explicit date range. applications_near() is a
    # Postgres FUNCTION that only accepts a simple lookback window
    # (p_days_back), not an explicit from/to range — changing that would
    # mean a real schema migration. Rather than touch the function, when
    # a real date range is given we widen the days_back passed INTO the
    # function (so its own internal filter can't accidentally exclude
    # something we actually want), then apply the precise date_from/
    # date_to boundary as an additional filter in THIS query, which has
    # direct access to a.submitted_date. No migration needed, and the
    # function's existing simple-lookback behaviour is untouched for
    # every caller that doesn't pass a range.
    effective_days = _widen_days_for_date_range(days, date_from)
    order_by = _resolve_sort_order(sort)

    rows = await db.fetch(f"""
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
    # BUG FIX (2026-08-11) — this was causing a genuine 500 on EVERY tag
    # page request, regardless of which sort was actually chosen. The
    # earlier version only special-cased "distance" as invalid here (no
    # applications_near() join in this query), but missed that BOTH
    # remaining SORT_OPTIONS entries (date_desc AND date_asc) ALSO
    # reference "an.distance_miles" as a secondary tiebreaker — a table
    # alias that doesn't exist in this query's FROM clause either way.
    # Postgres correctly rejected every single query with "missing
    # FROM-clause entry for table an", 100% of the time, since even the
    # DEFAULT sort fell into this trap. Fixed using TAG_SORT_OPTIONS
    # (see its own definition above), a genuinely separate mapping that
    # never references that alias at all.
    order_by = TAG_SORT_OPTIONS.get(sort, TAG_SORT_OPTIONS["date_desc"])

    # ADDED (2026-08-11) — keyword/sort/date range, reusing the exact
    # same keyword normalization already built and tested for the main
    # postcode search. No date-widening workaround needed here, unlike
    # _fetch_applications — this query goes straight against
    # planning_applications with a plain WHERE clause, not through the
    # applications_near() Postgres function that only accepts a simple
    # lookback window.
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
    """Councils that genuinely have at least one application under this
    tag — used to populate the filter dropdown, so it only ever lists
    real, useful options rather than every council in the system
    (including ones with zero matches for this particular tag)."""
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
                  status: Optional[str] = None, app_type: Optional[str] = None,
                  keyword: Optional[str] = None, sort: Optional[str] = None,
                  date_from: Optional[str] = None, date_to: Optional[str] = None):
    postcode = postcode.strip().upper()
    location = await postcode_lookup(postcode)
    # BUG FIX (2026-08-13) — parsed here, not via the route's own type
    # hint. See _parse_date_param's docstring for the full real-bug
    # writeup: an Optional[date] parameter gets validated by FastAPI
    # before our code runs, and an empty string (which the filter form
    # sends whenever the date fields are left blank) fails that
    # validation as malformed input rather than being treated as
    # "no filter" — every filter submission was 422ing site-wide.
    date_from_parsed = _parse_date_param(date_from)
    date_to_parsed = _parse_date_param(date_to)

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
                   lat, lng
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
        # council_name isn't in the row SELECT above (the whole page is
        # already scoped to one council) — added here just so the shared
        # flag helper below can reuse the same logic everywhere else.
        a["council_name"] = council["name"]

    _add_date_availability_flag(apps)

    council_dict = dict(council)
    council_dict["date_unavailable_note"] = council["name"] in COUNCILS_WITHOUT_DATE_DATA

    return render("council.html", {
        "request": request,
        "council": council_dict,
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


async def _render_tag_page(request: Request, tag: str, status: Optional[str],
                            council: Optional[str], keyword: Optional[str] = None,
                            sort: Optional[str] = None,
                            date_from: Optional[str] = None,
                            date_to: Optional[str] = None) -> HTMLResponse:
    # FIX (2026-07-30) — a real bug, not a guess: both dropdowns live in
    # the same <form>, so selecting one resubmits the other too. "Any
    # status"/"All councils" are <option value=""> — a GET form always
    # includes every field, so an untouched dropdown arrives as a
    # literal empty string ("status=&council=..."), not an absent
    # parameter. The SQL check only treated a genuinely MISSING value as
    # "no filter" — an empty string matched neither NULL nor any real
    # row, silently excluding everything regardless of the other
    # filter's real value. Confirmed via a real report: West Oxfordshire
    # visibly has a match, but selecting it still returned zero results,
    # because status="" was travelling along unnoticed in the same
    # request.
    status = status or None
    council = council or None
    # BUG FIX (2026-08-13) — date_from/date_to arrive here as raw
    # strings now (not Optional[date]) precisely so an empty string from
    # an untouched date field doesn't 422 before this function even
    # runs. See _parse_date_param's docstring for the full writeup.
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


@app.get("/councils", response_class=HTMLResponse)
async def councils_list(request: Request):
    async with get_db() as db:
        councils = await db.fetch("""
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
    for c in covered:
        c["area_aliases"] = COUNCIL_AREA_ALIASES.get(c["name"], [])
        # Lowercase combined name+aliases string for a simple client-side
        # search match — so searching "Harrogate" finds the real North
        # Yorkshire Council card, not just its own literal name.
        c["search_haystack"] = " ".join([c["name"]] + c["area_aliases"]).lower()
        # ADDED (2026-08-13) — a real, honest status (Live/Delayed/
        # Offline), computed from last_saved_at (when WE last actually
        # scraped successfully), not latest_date (when the most recent
        # APPLICATION was submitted — a genuinely different thing: a
        # council can have old applications but still be scraping fine
        # nightly, or have recent applications sitting there while our
        # own scraper has actually stopped running).
        days_since_save = (
            (date.today() - c["last_saved_at"].date()).days
            if c["last_saved_at"] else None
        )
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


# Real, confirmed reasons for specific councils that have gone quiet —
# only councils we've actually manually diagnosed with real evidence
# this session, not a guess. Anything not listed here still shows on
# the coverage-gaps page (using last_saved_at, which we do have), just
# without inventing a specific cause we haven't actually confirmed.
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

# How many days without a successful save before we consider a
# previously-working council to be a genuine gap, not just a quiet
# night. Generous enough to avoid flagging a single bad run, tight
# enough to catch a real, sustained problem quickly. Also reused below
# by _coverage_status() as the "Offline" boundary, so both pages agree
# on what "gone quiet" actually means rather than using two different,
# silently inconsistent thresholds.
GAP_THRESHOLD_DAYS = 10

# ADDED (2026-08-13) — a genuine three-state status, not just the
# existing covered/manual_link/pending split, which only answers
# whether a council has EVER been covered, not whether it's currently
# healthy. "Delayed" is a real middle state that neither existing page
# currently shows at all — /coverage-gaps only surfaces things already
# past the full GAP_THRESHOLD_DAYS, nothing shows "starting to look a
# bit stale but not a confirmed gap yet". 2 days allows for one missed
# night (matches the real nightly cadence) without unduly alarming.
DELAYED_THRESHOLD_DAYS = 2


def _coverage_status(coverage_source: str, days_since_save: Optional[int]) -> dict:
    """Real, honest status computed from data we already store — no new
    columns or migration needed. days_since_save should be
    (CURRENT_DATE - last_saved_at::date), the same real, stored field
    /coverage-gaps already uses, NOT the most recent application's own
    submitted_date (a genuinely different thing: when data was last
    scraped vs. how recent the data itself is)."""
    if coverage_source in ("pending", "none", "manual_link"):
        return {"key": "offline", "emoji": "🔴", "label": "Not yet covered"}
    if days_since_save is None:
        return {"key": "offline", "emoji": "🔴", "label": "Offline"}
    if days_since_save >= GAP_THRESHOLD_DAYS:
        return {"key": "offline", "emoji": "🔴", "label": "Offline"}
    if days_since_save >= DELAYED_THRESHOLD_DAYS:
        return {"key": "delayed", "emoji": "🟠", "label": "Delayed"}
    return {"key": "live", "emoji": "🟢", "label": "Live"}


# Real, confirmed areas covered by a single merged scraper entry — NOT
# separate councils, and deliberately NOT counted separately in
# covered_count. Some councils' modern unitary portal genuinely merges
# several former district councils into one search (confirmed via real
# evidence, e.g. North Yorkshire's own "Hello and welcome to Public
# Access for Harrogate, Scarborough, Craven, Hambleton and Selby
# Planning Areas" notice). Counting each historic name as its own
# "covered" entry would inflate the headline number in a misleading way
# — if the one real portal behind them goes down, all of them would go
# dark from a single root cause, not independent problems. Shown as
# searchable aliases instead, so someone looking for "Harrogate" or
# "Craven" specifically can still find a clear answer, correctly
# attributed to the real underlying council.
COUNCIL_AREA_ALIASES = {
    "North Yorkshire Council": [
        "Harrogate", "Scarborough", "Craven", "Hambleton", "Selby",
    ],
}



@app.get("/coverage-gaps", response_class=HTMLResponse)
async def coverage_gaps(request: Request):
    """Honest, specific transparency about councils that WERE working and
    have since gone quiet — deliberately distinct from /councils' three
    buckets, which are about whether a council has EVER been covered.
    This page is about regressions: real data existed, collection has
    since stopped. Inspired directly by a comparable competitor's
    "Known Data Gaps" page, which names the exact councils, the exact
    date, and what date the data is frozen at — the same standard we're
    matching here.

    HONEST LIMITATION: our diagnostics currently only print the specific
    failure reason (timeout vs WAF vs 404 etc.) to console logs during a
    scrape run — they aren't persisted anywhere in the database. This
    page can reliably say a council has gone quiet and since when
    (last_saved_at is real, stored data), but can only give a specific
    root cause for the handful of councils in KNOWN_GAP_REASONS above,
    which we've actually manually diagnosed with real evidence.
    Everything else gets an honest "no new data since X" without
    inventing a cause we haven't confirmed.
    """
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
    """Checks the LAST slash-separated segment of a reference number for
    a real outline suffix (e.g. '26/01234/OUT', '26/01234/OUT1' for
    phased outlines, '26/01234/OUTEIA' for outline with EIA) — not just
    any string ending in the letters "out", to avoid false-matching
    something coincidental elsewhere in a longer reference."""
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
    # FIX (2026-07-30) — real, confirmed gap: some councils' own
    # application_type field comes back blank or unhelpful for a given
    # record (the FIELD DIAGNOSTIC / DATE LABEL DIAGNOSTIC warnings seen
    # scattered through recent scrape logs are exactly this situation).
    # In those cases the only remaining signal is the reference number
    # itself — an outline application with a blank type field was
    # falling through to "other"/not-major entirely, missing real major
    # developments.
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
    # Same fallback as _is_major above — only reached when the type
    # field gave us nothing usable to match against.
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
