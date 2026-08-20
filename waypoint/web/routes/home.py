"""The morning scan, in three bands (§12)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from waypoint.metrics import board, flow, risks
from waypoint.metrics.status import BOARD, GITHUB_PRS, JIRA_ISSUES, EVERYTHING, panel_status
from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()
VISIBLE_ROWS = 4


@dataclass(frozen=True)
class QueueItem:
    ref: str
    title: str
    meta: str
    right: str
    url: str


@dataclass(frozen=True)
class Queue:
    label: str
    items: list[QueueItem]
    shown: list[QueueItem]
    more: int
    empty_message: str | None
    status: object


def _queue(label, items, empty_message, status) -> Queue:
    return Queue(
        label=label,
        items=items,
        shown=items[:VISIBLE_ROWS],
        more=max(0, len(items) - VISIBLE_ROWS),
        empty_message=None if items else empty_message,
        status=status,
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request, ctx: PageContext = Depends(page_context)) -> HTMLResponse:
    if not ctx.synced:
        return templates.TemplateResponse(request, "empty.html", {"ctx": ctx, "page": "home"})

    con, now = ctx.con, ctx.now
    strip = board.board_strip(con, now=now)
    register = risks.rule_risks(con, ctx.cfg, now=now)

    from waypoint import skills_runner
    from waypoint.metrics.risks import Evidence, Risk
    from waypoint.metrics.status import stale_status
    from waypoint.store.reports import ReportStore

    spec = skills_runner.SKILLS["delivery-risk"]
    report = ReportStore(ctx.root).latest(spec.name)
    report_status = None
    merged = list(register.items)
    if report is not None and not report.malformed:
        report_status = stale_status(report.inputs_digest, ctx.manifest, report.generated_at)
        for item in report.items:
            merged.append(
                Risk(
                    rule="skill", severity=item.severity, title=item.title,
                    detail=item.body,
                    evidence=[
                        Evidence(source.get("type", "item"), source.get("ref", ""),
                                 source.get("url", ""))
                        for source in item.evidence
                    ],
                    age_days=0.0, age_text="", origin="skill",
                )
            )
        merged.sort(key=lambda risk: (
            {"high": 0, "med": 1, "low": 2}[risk.severity], -risk.age_days, risk.rule
        ))

    open_prs = [
        QueueItem(ref=item.id, title=item.title, meta=item.repo_id,
                  right=item.review_wait_text, url=item.url)
        for item in flow.open_prs(con)
    ]
    in_flight = [
        QueueItem(item.key, item.summary, item.column, item.age_text, item.url)
        for item in board.in_flight(con, now=now)
    ]
    blocked = [
        QueueItem(item.key, item.summary, item.status, "blocked", item.url)
        for item in board.blocked_issues(con)
    ]

    context = {
        "ctx": ctx,
        "page": "home",
        "strip": strip,
        "strip_status": panel_status(ctx.manifest, BOARD),
        "throughput": flow.throughput(con, now=now),
        "register": register,
        "register_status": panel_status(ctx.manifest, EVERYTHING),
        "register_items": merged,
        "report": report,
        "report_status": report_status,
        "spec": spec,
        "claude_present": skills_runner.claude_available(),
        "person_query": "",
        "queues": [
            _queue("open PRs", open_prs, "No open pull requests.",
                   panel_status(ctx.manifest, GITHUB_PRS)),
            _queue("issues in flight", in_flight, "Nothing in flight.",
                   panel_status(ctx.manifest, JIRA_ISSUES)),
            _queue("blocked", blocked, "Nothing blocked.",
                   panel_status(ctx.manifest, JIRA_ISSUES)),
        ],
    }
    return templates.TemplateResponse(request, "home.html", context)
