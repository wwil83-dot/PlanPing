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


async def _fetch_applications(db, lat: float, lng: float, radius: float, days: int,
                               status: Optional[str] = None,
                               app_type: Optional[str] = None) -> list[dict]:
    """Shared query + classification logic for /search and /search.csv —
    factored out so both routes stay in sync rather than risking two
    slightly different copies of the same query drifting apart over time.

    status filters directly in SQL — safe, because every scraper already
    normalizes status to a fixed set (pending/approved/refused/withdrawn)
    before writing to the database, via each scraper's own
    _normalise_status() function.

    app_type filtering happens in Python AFTER fetching, matching against
    the existing _type_badge() classification — deliberately NOT
    replicated as SQL ILIKE logic, since application_type is messy
    free text straight from council portals and duplicating the
    classification rules in two places would risk them silently drifting
    out of sync. Result sets for a single postcode search are naturally
    small (radius-bounded), so filtering in Python costs nothing
    meaningful in practice.
    """
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

    return applications


# TIER 3 — smart tagging (2026-07-20). Tags are precomputed and stored on
# each row by a Postgres trigger (see migrations/migration_smart_tags.sql)
# rather than classified live in Python like _type_badge() — deliberately,
# because these three pages search the WHOLE database by category, not a
# postcode-radius-bounded result set, so running regex per-request over
# every row wouldn't scale the way _type_badge() reasonably does for a
# small radius search. One shared engine, three thin pages on top of it,
# per the original Tier 3 roadmap note.
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
    """Shared query for the three Tier 3 tag pages — searches the full
    database by precomputed tag, not a postcode radius. Kept as its own
    helper (not folded into _fetch_applications) because the underlying
    query is genuinely different in shape: no applications_near() radius
    function, no lat/lng/distance, and it needs its own LIMIT since an
    unbounded national query has no natural result-set ceiling the way a
    radius search does.

    HONEST LIMITATION (documented in the migration too, repeated here
    since it directly affects what this function returns): tags are
    keyword/regex classification against free text from 130+ councils
    with inconsistent phrasing. This will have false negatives (real
    matches missed) and occasional false positives — a reasonable first
    pass, not a guarantee.
    """
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

    return applications


# Filter dropdown options, matching exactly what _type_badge()/_status_class()
# actually produce — kept as a single source of truth here so the UI
# dropdowns can never silently drift out of sync with the real
# classification categories.
STATUS_FILTER_OPTIONS = ["pending", "approved", "refused", "withdrawn"]
TYPE_FILTER_OPTIONS = ["householder", "full", "outline", "listed", "tree",
                       "advert", "prior", "major", "other"]


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, postcode: str, radius: float = 1.0, days: int = 30,
                  status: Optional[str] = None, app_type: Optional[str] = None):
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
        applications = await _fetch_applications(db, lat, lng, radius, days, status, app_type)

        # Check if this council is covered
        council = await db.fetchrow("""
            SELECT id, name, slug, coverage_source, portal_url, system
            FROM councils
            WHERE name ILIKE $1
               OR name ILIKE $2
            LIMIT 1
        """, f"%{council_name}%", f"{council_name}%")

    # BUG FIX (2026-07-16): the results-page map used to serialize entire
    # application dicts straight to JSON in the template via Jinja's
    # tojson filter. Those dicts include submitted_date/decision_date as
    # real Python date objects — plain JSON has no concept of a date, so
    # trying to serialize one raises an unhandled exception, which turned
    # into the 500 error on every postcode search. Building a small,
    # explicitly JSON-safe subset here (only int/float/str/bool fields)
    # avoids the problem at the source rather than trying to work around
    # it in the template.
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
    """CSV export — reuses the exact same _fetch_applications() helper as
    /search, so results are guaranteed identical to whatever's on screen,
    just in downloadable form. Built for the planning-consultants segment
    specifically (explicitly requested: "export to CSV").
    """
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
    """Batch multiple postcode searches into one combined result set —
    reuses the exact same _fetch_applications() helper as /search and
    /search.csv, called once per postcode. Built for the planning
    consultants segment specifically (explicitly requested: "bulk
    searches"). Capped at 50 postcodes per submission — generous enough
    for real consultant workflows, bounded enough to avoid one submission
    accidentally hammering the postcode-lookup service or the database
    with an unbounded batch.
    """
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

            # Dedupe across postcodes — an application within radius of
            # TWO searched postcodes should appear once in the combined
            # list, not twice, while still being counted correctly in
            # each individual postcode's own breakdown above.
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
    """CSV export for the combined bulk search — same collection logic as
    bulk_search() above, same CSV-writing pattern as /search.csv, applied
    to the deduplicated combined set across every postcode submitted.
    """
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
    """Free-text address/street search — distinct from the postcode+radius
    search on the main results page. Deliberately simple: an ILIKE match
    against the existing address field, no new data source needed.

    Honest limit worth keeping in mind: this only surfaces what's
    actually been scraped, which currently goes back as far as bulk
    mode's window (~180 days) for most councils — not genuine multi-year
    "history" yet. The template says so directly rather than implying
    more depth than the data actually has.
    """
    applications = []
    q_clean = (q or "").strip()

    if q_clean and len(q_clean) >= 3:  # avoid overly broad 1-2 char matches
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

        # "Neighbouring applications" — reuses the exact same
        # _fetch_applications() helper Tier 1 built for /search, just
        # centered on THIS application's own coordinates instead of a
        # postcode lookup. Deliberately tighter radius (0.3 miles, not
        # the search page's 1-mile default) since "what's happening on
        # this street" should mean genuinely nearby, not a whole
        # postcode's worth of area. Deliberately generous days (365) so
        # this reads as real local context/history, not just "the last
        # month" — bounded naturally by however far back your data
        # actually goes (currently ~180 days via bulk mode), so this
        # isn't overselling anything, just not artificially limiting it
        # either.
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
    # "Today" for NEW APPLICATIONS means the application's own real
    # submitted_date as recorded by the council — NOT "records we
    # happened to scrape in the last 24 hours". A council might publish
    # an application dated 3 days ago that we're only seeing for the
    # first time today (normal, given the rolling 14-day scrape window);
    # that should NOT count as "new today" here, since it genuinely
    # wasn't submitted today.
    #
    # "Today" for APPROVED/REFUSED is DIFFERENT, and deliberately so —
    # see migration_decision_detected.sql for the full story. Confirmed
    # this session, via a diagnostic firing identically across 5+
    # unrelated councils, that a council's OFFICIAL decision_date
    # genuinely isn't present on the Idox monthly-list pages being
    # scraped (not a mislabeled field — actually absent). Filtering on
    # decision_date = CURRENT_DATE was therefore returning 0 almost every
    # day, regardless of how much real decision activity happened.
    # decision_detected_at answers a different, honestly-achievable
    # question instead: "when did PlanFind FIRST observe this application
    # had been decided?" — populated by a database trigger that fires
    # only on a genuine transition into approved/refused, not on every
    # routine re-scrape of an application already sitting in that state.
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
                -- NOTE: appeals are NOT a distinct tracked field anywhere
                -- in the scraper schema — this is a best-effort match on
                -- application_type text mentioning "appeal", not a
                -- guaranteed-accurate count the way the three stats above
                -- are. Flagged clearly in the template too.
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
    """Approval-rate analytics — which councils approve the highest/lowest
    proportion of decided applications. Deliberately simple: a plain SQL
    aggregation over the existing normalized status field, no new data
    source needed. HAVING >= 10 decided applications avoids small-sample
    councils showing a misleading 100%/0% rate off just one or two
    decisions.
    """
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
    """Shared body for all three Tier 3 tag routes below — same shape of
    factoring as _fetch_applications/_fetch_tagged_applications, so the
    three pages can't silently drift into three different implementations
    of what is genuinely the same operation with a different tag."""
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
