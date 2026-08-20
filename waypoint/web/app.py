"""FastAPI app factory. Fixed nav, no client-side routing (UI§9)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from waypoint.errors import WaypointError
from waypoint.web.deps import templates

STATIC_DIR = Path(__file__).parent / "static"


def create_app(project_dir: Path) -> FastAPI:
    app = FastAPI(title="Waypoint", docs_url=None, redoc_url=None)
    app.state.project_dir = Path(project_dir)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    reports_dir = Path(project_dir) / ".waypoint" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/reports", StaticFiles(directory=str(reports_dir)), name="reports")

    @app.exception_handler(WaypointError)
    async def waypoint_error_handler(request: Request, exc: WaypointError) -> HTMLResponse:
        """A missing or broken config must not 500 (UI§9): name it and the fix.

        This fires before a `PageContext` can be built, so it renders a
        standalone page rather than `base.html`, which depends on one.
        """
        return templates.TemplateResponse(
            request, "setup_needed.html", {"message": exc.message}, status_code=200
        )

    from waypoint.web.routes import analyze, delivery, home, people, sync

    app.include_router(home.router)
    app.include_router(delivery.router)
    app.include_router(people.router)
    app.include_router(sync.router)
    app.include_router(analyze.router)
    return app
