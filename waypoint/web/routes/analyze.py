"""`POST /analyze/{skill}` runs a skill; HTMX polls for progress (§12)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from waypoint import skills_runner
from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()


@dataclass(frozen=True)
class AnalyzeState:
    slug: str
    running: bool
    message: str


def _running_map(request: Request) -> dict[str, str]:
    # Override 3: per-app state, not a module-level dict — a module-level
    # dict would be shared (and never cleared) across every create_app()
    # instance and leak state between tests and between app instances.
    if not hasattr(request.app.state, "analyze_running"):
        request.app.state.analyze_running = {}
    return request.app.state.analyze_running


def _partial(request: Request, ctx: PageContext, slug: str, message: str, running: bool):
    return templates.TemplateResponse(
        request,
        "partials/analyze_state.html",
        {"ctx": ctx, "state": AnalyzeState(slug=slug, running=running, message=message)},
    )


@router.post("/analyze/{slug}", response_class=HTMLResponse)
def analyze(
    request: Request,
    slug: str,
    background: BackgroundTasks,
    person: str | None = Query(None),
    ctx: PageContext = Depends(page_context),
) -> HTMLResponse:
    if slug not in skills_runner.SKILLS:
        raise HTTPException(status_code=404, detail="Unknown skill.")
    if not skills_runner.claude_available():
        return _partial(request, ctx, slug,
                        "Generated analysis is unavailable — Claude Code is not installed.", False)

    running = _running_map(request)
    key = f"{slug}:{person or ''}"
    if running.get(key) == "running":
        return _partial(request, ctx, slug, "already running", True)
    running[key] = "running"

    def task() -> None:
        outcome = skills_runner.run_skill(ctx.project_dir, slug, person_id=person)
        running[key] = "done" if outcome.ok else outcome.message

    background.add_task(task)
    return _partial(request, ctx, slug, "running", True)


@router.get("/analyze/{slug}/status", response_class=HTMLResponse)
def analyze_status(
    request: Request,
    slug: str,
    person: str | None = Query(None),
    ctx: PageContext = Depends(page_context),
) -> HTMLResponse:
    running = _running_map(request)
    state = running.get(f"{slug}:{person or ''}", "idle")
    return _partial(request, ctx, slug, state, state == "running")
