"""The roster is cards; the person page is the 1:1 page.

A table would satisfy the letter of the rules while breaking the principle in
§4, because column alignment IS side-by-side comparison. Any change that
regularises card contents into aligned rows reintroduces the comparison the
principle exists to prevent (§12, UI§5).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from waypoint import clock, skills_runner
from waypoint.metrics import people as people_metrics
from waypoint.metrics.status import EVERYTHING, panel_status
from waypoint.roster import Roster
from waypoint.store.reports import ReportStore
from waypoint.store.views import PersonViews
from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()

STANDING_NOTE = "signals to ask about, not measures of performance"


@router.get("/people", response_class=HTMLResponse)
def roster(request: Request, ctx: PageContext = Depends(page_context)) -> HTMLResponse:
    if not ctx.synced:
        return templates.TemplateResponse(request, "empty.html", {"ctx": ctx, "page": "people"})
    cards = people_metrics.roster_cards(
        ctx.con, Roster.from_config(ctx.cfg), now=ctx.now, thresholds=ctx.cfg.thresholds
    )
    return templates.TemplateResponse(
        request,
        "people.html",
        {
            "ctx": ctx,
            "page": "people",
            "cards": cards,
            "note": STANDING_NOTE,
            "status": panel_status(ctx.manifest, EVERYTHING),
        },
    )


@router.get("/people/{person_id}", response_class=HTMLResponse)
def person(
    request: Request,
    person_id: str,
    since: str | None = Query(None),
    ctx: PageContext = Depends(page_context),
) -> HTMLResponse:
    person_record = Roster.from_config(ctx.cfg).by_id(person_id)
    if person_record is None:
        raise HTTPException(status_code=404, detail="No such person in the roster.")
    if not ctx.synced:
        return templates.TemplateResponse(request, "empty.html", {"ctx": ctx, "page": "people"})

    views = PersonViews(ctx.root)
    default_window = views.last_viewed(person_id) or (ctx.now - timedelta(days=14))
    if since:
        try:
            window_start = clock.parse(since + "T00:00:00Z")
        except ValueError:
            window_start = default_window
    else:
        window_start = default_window
    views.record(person_id, ctx.now)

    view = people_metrics.person_view(
        ctx.con, person_record, now=ctx.now, since=window_start, work_mix=ctx.cfg.work_mix
    )
    spec = skills_runner.SKILLS["one-on-one-prep"]
    return templates.TemplateResponse(
        request,
        "person.html",
        {
            "ctx": ctx,
            "page": "people",
            "view": view,
            "note": STANDING_NOTE,
            "status": panel_status(ctx.manifest, EVERYTHING),
            "spec": spec,
            "report": ReportStore(ctx.root).latest(spec.name, person_id=view.person_id),
            "claude_present": skills_runner.claude_available(),
            "person_query": "?person=" + view.person_id,
        },
    )
