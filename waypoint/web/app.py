"""FastAPI app factory. Fixed nav, no client-side routing (UI§9)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"


def create_app(project_dir: Path) -> FastAPI:
    app = FastAPI(title="Waypoint", docs_url=None, redoc_url=None)
    app.state.project_dir = Path(project_dir)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from waypoint.web.routes import delivery, home, people, sync

    app.include_router(home.router)
    app.include_router(delivery.router)
    app.include_router(people.router)
    app.include_router(sync.router)
    return app
