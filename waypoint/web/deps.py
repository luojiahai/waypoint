"""Per-request access to config, the index, and sync state.

The web layer renders and never computes (§6): everything here is lookup and
formatting, and every figure on a page came out of `metrics/`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from waypoint import clock
from waypoint.config import Config, load_config
from waypoint.metrics import charts
from waypoint.store import index as index_store
from waypoint.store.manifest import Manifest, ManifestStore
from waypoint.sync import Progress, read_progress

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["charts"] = charts


@dataclass
class PageContext:
    project_dir: Path
    root: Path
    cfg: Config
    manifest: Manifest
    con: sqlite3.Connection | None
    now: datetime
    synced: bool
    sync_label: str
    sync_state: str
    progress: Progress | None = None


def _sync_label(manifest: Manifest, now: datetime) -> tuple[str, str]:
    run = manifest.last_run()
    if run is None or run.finished_at is None:
        return "never synced", ""
    stamp = clock.parse(run.finished_at)
    clock_text = stamp.strftime("%H:%M")
    if run.status == "failed":
        return f"last sync failed · {clock_text}", "failed"
    if run.status == "partial":
        return f"last sync partial · {clock_text}", "partial"
    hours = (now - stamp).total_seconds() / 3600
    ago = f"{hours:.0f}h ago" if hours >= 1 else f"{hours * 60:.0f}m ago"
    return f"synced {clock_text} · {ago}", ""


def page_context(request: Request) -> PageContext:
    project_dir: Path = request.app.state.project_dir
    root = project_dir / ".waypoint"
    cfg = load_config(root)
    manifest = ManifestStore(root).load()
    database = root / "index.db"
    con = index_store.connect(database, read_only=True) if database.exists() else None
    now = clock.now()
    label, state = _sync_label(manifest, now)
    progress_path = root / "state" / "progress.json"
    progress = read_progress(root) if progress_path.exists() else None
    return PageContext(
        project_dir=project_dir,
        root=root,
        cfg=cfg,
        manifest=manifest,
        con=con,
        now=now,
        synced=con is not None and manifest.last_run() is not None,
        sync_label=label,
        sync_state=state,
        progress=progress,
    )
