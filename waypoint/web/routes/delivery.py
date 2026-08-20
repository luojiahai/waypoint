"""Board, epics, and flow — one scrolling page (§12)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from waypoint.metrics import board, epics, flow
from waypoint.metrics.status import BOARD, EVERYTHING, GITHUB_PRS, JIRA_ISSUES, panel_status
from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()


@dataclass(frozen=True)
class FlowCard:
    label: str
    primary: str
    secondary: str
    spark: object
    kind: str


@router.get("/delivery", response_class=HTMLResponse)
def delivery(
    request: Request,
    weeks: int = Query(12, ge=4, le=52),
    ctx: PageContext = Depends(page_context),
) -> HTMLResponse:
    if not ctx.synced:
        return templates.TemplateResponse(request, "empty.html", {"ctx": ctx, "page": "delivery"})

    con, now = ctx.con, ctx.now
    latency = flow.review_latency(con, now=now, weeks=weeks)
    cycle = flow.issue_cycle_time(con, now=now, weeks=weeks)
    wip = flow.wip_series(con, now=now, weeks=weeks)
    throughput = flow.throughput(con, now=now, weeks=weeks)

    context = {
        "ctx": ctx,
        "page": "delivery",
        "weeks": weeks,
        "week_options": (4, 12, 26, 52),
        "strip": board.board_strip(con, now=now),
        "aging": board.aging_section(
            con, now=now, threshold_days=ctx.cfg.thresholds.issue_aging_days
        ),
        "board_status": panel_status(ctx.manifest, BOARD),
        "epics_section": epics.epics(con, now=now, jira=ctx.cfg.jira),
        "epics_status": panel_status(ctx.manifest, JIRA_ISSUES),
        "flow_status": panel_status(ctx.manifest, EVERYTHING),
        "pr_status": panel_status(ctx.manifest, GITHUB_PRS),
        "flow_panels": [
            FlowCard("PR review latency", latency.median_text,
                     f"p75 {latency.p75_text} · n={latency.count}", latency.spark, "line"),
            FlowCard("Issue cycle time", cycle.median_text,
                     f"p75 {cycle.p75_text} · n={cycle.count}", cycle.spark, "line"),
            FlowCard("WIP", str(wip.current),
                     f"median {wip.median:.0f}" if wip.median is not None else "median —",
                     wip.spark, "line"),
            FlowCard("Weekly throughput", str(throughput.current),
                     throughput.summary, throughput.spark, "bars"),
        ],
    }
    return templates.TemplateResponse(request, "delivery.html", context)
