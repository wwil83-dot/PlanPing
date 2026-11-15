"""
Trade-leads, step 1: free "patch checker" + waitlist.   (app/leads.py)

Add to app/main.py (two lines):
    from app.leads import router as leads_router
    app.include_router(leads_router)

Counts only — no addresses, no names. Addresses are the paid product later.
"""
import re, time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from app.db import get_db
from app.geocode import postcode_lookup
from app.worktype import TRADES, WORK_TYPES

router = APIRouter()
_jinja = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)

RADII = [1, 3, 5, 10]
_hits: dict = defaultdict(deque)


def _limited(request: Request, per_minute: int = 20) -> bool:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "x")).split(",")[0].strip()
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        return True
    q.append(now)
    if len(_hits) > 5000:
        _hits.clear()
    return False


def _page(request, **ctx):
    base = dict(request=request, trades=TRADES, radii=RADII, work_types=WORK_TYPES,
                postcode="", trade="", radius=3, result=None, error=None)
    base.update(ctx)
    return HTMLResponse(_jinja.get_template("patch_checker.html").render(**base))


@router.get("/patch-checker", response_class=HTMLResponse)
async def patch_checker(request: Request, postcode: Optional[str] = None,
                        trade: Optional[str] = None, radius: int = 3):
    postcode = (postcode or "").strip().upper()
    trade = trade or ""
    radius = radius if radius in RADII else 3
    if not postcode or trade not in TRADES:
        return _page(request, postcode=postcode, trade=trade, radius=radius)
    if _limited(request):
        return _page(request, postcode=postcode, trade=trade, radius=radius,
                     error="Lots of searches just now — please try again in a minute.")

    location = await postcode_lookup(postcode)
    if not location:
        return _page(request, postcode=postcode, trade=trade, radius=radius,
                     error=f"We couldn't find the postcode “{postcode}”. Check it and try again.")

    wanted = TRADES[trade][1]
    async with get_db() as db:
        rows = await db.fetch("""
            SELECT a.work_type,
                   COUNT(*) AS n90,
                   COUNT(*) FILTER (WHERE a.submitted_date >= CURRENT_DATE - 30) AS n30
            FROM applications_near($1, $2, $3, 90) an
            JOIN planning_applications a ON a.id = an.application_id
            WHERE a.work_type = ANY($4::text[])
            GROUP BY a.work_type
            ORDER BY n90 DESC
        """, location["lat"], location["lng"], float(radius), wanted)
    breakdown = [dict(work_type=r["work_type"], label=WORK_TYPES.get(r["work_type"], r["work_type"]),
                      n30=r["n30"], n90=r["n90"]) for r in rows]
    result = dict(total30=sum(b["n30"] for b in breakdown),
                  total90=sum(b["n90"] for b in breakdown),
                  breakdown=breakdown, trade_label=TRADES[trade][0])
    return _page(request, postcode=postcode, trade=trade, radius=radius, result=result)


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/api/lead-waitlist")
async def lead_waitlist(request: Request, email: str = Form(...), trade: str = Form(...),
                        postcode: str = Form(...), radius: int = Form(3)):
    email = email.strip().lower()
    postcode = postcode.strip().upper()
    if not _EMAIL.match(email) or trade not in TRADES or not postcode or len(postcode) > 10:
        return JSONResponse({"ok": False, "message": "Please check your details and try again."}, status_code=400)
    if _limited(request, per_minute=6):
        return JSONResponse({"ok": False, "message": "Too many requests — try again shortly."}, status_code=429)
    async with get_db() as db:
        await db.execute("""
            INSERT INTO lead_waitlist (email, trade, postcode, radius_miles)
            VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
        """, email, trade, postcode, radius)
    return {"ok": True, "message": "You're on the list — we'll email you when addresses for your patch are available."}
