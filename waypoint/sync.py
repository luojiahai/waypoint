"""fetch then build, once at a time.

Partial failure is the normal case for a daily two-API sync, not an exception
(§15): a source that fails is recorded as failed and the run continues, so a Jira
outage never discards what GitHub returned.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from waypoint import clock
from waypoint.config import Config, Secrets, load_config, load_secrets
from waypoint.errors import SourceError, WaypointError
from waypoint.sources.base import FAILED, EntityStatus, Source
from waypoint.sources.github import GithubSource
from waypoint.sources.http import HttpClient
from waypoint.sources.jira import JiraSource
from waypoint.store import index as index_store
from waypoint.store.manifest import ManifestStore
from waypoint.store.raw import RawStore


GITIGNORE = "raw/\nindex.db\nindex.db.tmp\nstate/\n"


@dataclass
class Progress:
    state: str = "idle"
    step: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    rate_limit: dict[str, dict] = field(default_factory=dict)


def ensure_workspace(root: Path) -> None:
    """Create `.waypoint/` and its .gitignore.

    `config.toml` and `reports/` stay committable so the user can keep report
    history in git if they want it (§7).
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE)


def _progress_path(root: Path) -> Path:
    return root / "state" / "progress.json"


def read_progress(root: Path) -> Progress:
    path = _progress_path(root)
    if not path.exists():
        return Progress()
    return Progress(**json.loads(path.read_text()))


def write_progress(root: Path, progress: Progress) -> None:
    path = _progress_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(progress), indent=2))


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SyncLock:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "state" / "sync.lock"

    def __enter__(self) -> SyncLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                holder = json.loads(self.path.read_text())
                pid = int(holder.get("pid", -1))
            except (ValueError, OSError):
                pid = -1
            if pid > 0 and _process_alive(pid):
                raise WaypointError(
                    "A sync is already running. Wait for it to finish, or delete "
                    f"{self.path} if you are certain it is not."
                )
        self.path.write_text(json.dumps({"pid": os.getpid(), "started_at": clock.iso(clock.now())}))
        return self

    def __exit__(self, *exc_info) -> None:
        self.path.unlink(missing_ok=True)


def build_sources(cfg: Config, secrets: Secrets) -> list[Source]:
    github_client = HttpClient(httpx.Client(timeout=30.0))
    jira_client = HttpClient(httpx.Client(timeout=30.0))
    return [
        GithubSource(cfg.github, secrets.github_token, http=github_client),
        JiraSource(cfg.jira, secrets.jira_email, secrets.jira_token, http=jira_client),
    ]


def run_sync(
    project_dir: Path,
    *,
    now: datetime,
    sources: Sequence[Source] | None = None,
    cfg: Config | None = None,
) -> Progress:
    project_dir = Path(project_dir)
    root = project_dir / ".waypoint"
    cfg = cfg or load_config(root)
    if sources is None:
        sources = build_sources(cfg, load_secrets(project_dir))

    ensure_workspace(root)
    run_id = clock.run_id(now)
    started = clock.iso(now)
    progress = Progress(state="running", step="fetch", started_at=started)
    write_progress(root, progress)

    raw = RawStore(root)
    manifest_store = ManifestStore(root)
    manifest = manifest_store.load()
    backfill_from = clock.iso(now - timedelta(days=cfg.sync.backfill_days))

    with SyncLock(root):
        try:
            for source in sources:
                progress.step = f"fetch {source.name}"
                write_progress(root, progress)
                since = {
                    entity: manifest.watermark(f"{source.name}/{entity}")
                    for entity in source.entities
                }
                try:
                    counts = raw.write(source.fetch(since), run_id)
                    statuses = source.status()
                except SourceError as exc:
                    counts = {}
                    statuses = {
                        entity: EntityStatus(entity, FAILED, 0, error=exc.message,
                                             watermark=since.get(entity) or backfill_from)
                        for entity in source.entities
                    }
                for entity, status in statuses.items():
                    status.count = counts.get(f"{source.name}/{entity}", status.count)
                manifest.record(source.name, statuses, run_id, clock.iso(now))
                manifest_store.save(manifest)
                progress.counts.update(counts)
                limits = getattr(getattr(source, "http", None), "rate_limit", None)
                if limits is not None:
                    progress.rate_limit[source.name] = {
                        "remaining": limits.remaining,
                        "reset_at": limits.reset_at,
                        "waited_seconds": limits.waited_seconds,
                    }
                write_progress(root, progress)

            progress.step = "build"
            write_progress(root, progress)
            index_store.build(root, cfg, now=now)

            con = index_store.connect(root / "index.db")
            run = manifest.last_run()
            con.execute(
                "INSERT OR REPLACE INTO sync_runs VALUES (?,?,?,?,?,?)",
                (
                    run_id, started, clock.iso(now),
                    run.status if run else "ok",
                    json.dumps(progress.counts), manifest.digest(),
                ),
            )
            con.commit()
            con.close()

            progress.state = "done"
            progress.step = "complete"
            progress.finished_at = clock.iso(now)
            progress.message = f"{sum(progress.counts.values())} records"
        except WaypointError as exc:
            progress.state = "failed"
            progress.message = exc.message
            progress.finished_at = clock.iso(now)
        write_progress(root, progress)
    return progress
