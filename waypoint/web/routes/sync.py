"""Sync state, and the only two controls in the app that start work.

`POST /sync` starts a background task that runs fetch then build; HTMX polls a
status endpoint for progress. No websockets, no job queue (§6).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse

from waypoint import clock
from waypoint.config import load_secrets
from waypoint.errors import WaypointError
from waypoint.metrics.people import unattributed_identities
from waypoint.sync import Progress, lock_holder_alive, read_progress, run_sync, write_progress
from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()
logger = logging.getLogger(__name__)

BOT_HINT = "add to github.bot_logins, or add the person to [[people]]"
JIRA_HINT = "add the account to [[people]]; activity is counted as unattributed until then"


def _state_partial(request: Request, ctx: PageContext) -> HTMLResponse:
    progress = read_progress(ctx.root)
    return templates.TemplateResponse(
        request, "partials/sync_state.html", {"ctx": ctx, "progress": progress}
    )


@router.get("/sync", response_class=HTMLResponse)
def sync_page(request: Request, ctx: PageContext = Depends(page_context)) -> HTMLResponse:
    entities = [ctx.manifest.entities[key] for key in sorted(ctx.manifest.entities)]
    unattributed = []
    if ctx.con is not None:
        unattributed = [
            {
                "source": row.source,
                "identity": row.identity,
                "kind": row.kind,
                "count": row.count,
                "hint": BOT_HINT if row.source == "github" else JIRA_HINT,
            }
            for row in unattributed_identities(ctx.con)
        ]
    return templates.TemplateResponse(
        request,
        "sync.html",
        {
            "ctx": ctx,
            "page": "sync",
            "entities": entities,
            "progress": read_progress(ctx.root),
            "unattributed": unattributed,
        },
    )


@router.get("/sync/status", response_class=HTMLResponse)
def sync_status(request: Request, ctx: PageContext = Depends(page_context)) -> HTMLResponse:
    return _state_partial(request, ctx)


@router.post("/sync", response_class=HTMLResponse)
def start_sync(
    request: Request, background: BackgroundTasks, ctx: PageContext = Depends(page_context)
) -> HTMLResponse:
    secrets = load_secrets(ctx.project_dir)
    if secrets.missing():
        write_progress(
            ctx.root,
            Progress(
                state="failed",
                message="Missing credentials: " + ", ".join(secrets.missing()),
                finished_at=clock.iso(ctx.now),
            ),
        )
        return _state_partial(request, ctx)

    if lock_holder_alive(ctx.root):
        write_progress(
            ctx.root,
            Progress(state="running", step="a sync is already running",
                     message="A sync is already running."),
        )
        return _state_partial(request, ctx)

    write_progress(ctx.root, Progress(state="running", step="starting",
                                      started_at=clock.iso(ctx.now)))

    def task() -> None:
        try:
            run_sync(ctx.project_dir, now=clock.now())
        except WaypointError as exc:
            write_progress(
                ctx.root,
                Progress(state="failed", message=exc.message, finished_at=clock.iso(clock.now())),
            )
        except Exception:
            # Never leave the UI stuck on "syncing" for an error we didn't
            # anticipate. The real exception is logged server-side only —
            # nothing about it (which could echo request/response detail)
            # reaches progress.json or the page.
            logger.exception("Unexpected error during background sync")
            write_progress(
                ctx.root,
                Progress(
                    state="failed",
                    message=(
                        "Unexpected error during sync. Check the server log, then "
                        "press Sync to try again."
                    ),
                    finished_at=clock.iso(clock.now()),
                ),
            )

    background.add_task(task)
    return _state_partial(request, ctx)
