"""`waypoint serve | sync | build | doctor | query | capture-fixtures`.

Every command reports a `WaypointError` as its message and exit code 1. A user
who mistyped a repo name should read one sentence, not a traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer

from waypoint import clock
from waypoint.config import load_config
from waypoint.errors import WaypointError
from waypoint.store import index as index_store
from waypoint.sync import run_sync

app = typer.Typer(add_completion=False, help="Local delivery and people dashboard.")


def _root(directory: Path | None) -> Path:
    return (directory or Path.cwd()) / ".waypoint"


def _fail(exc: WaypointError) -> NoReturn:
    typer.echo(exc.message)
    raise typer.Exit(code=1)


@app.command()
def build(directory: Path = typer.Option(None, "--dir", help="Project directory")) -> None:
    """Rebuild the index from existing raw data."""
    project = directory or Path.cwd()
    try:
        cfg = load_config(_root(project))
        result = index_store.build(_root(project), cfg, now=clock.now())
    except WaypointError as exc:
        _fail(exc)
    total = sum(result.tables.values())
    typer.echo(f"Rebuilt index: {total} rows across {len(result.tables)} tables.")
    if result.unattributed:
        typer.echo(f"{len(result.unattributed)} unrostered identities — see the Sync page.")


@app.command()
def sync(directory: Path = typer.Option(None, "--dir", help="Project directory")) -> None:
    """Fetch from every source, then build. The same path the Sync button uses."""
    project = directory or Path.cwd()
    try:
        from waypoint.config import load_secrets

        cfg = load_config(_root(project))
        secrets = load_secrets(project)
        if secrets.missing():
            raise WaypointError(
                "Missing credentials: " + ", ".join(secrets.missing())
                + ". Set them in the environment or a gitignored .env, then run `waypoint doctor`."
            )
        progress = run_sync(project, now=clock.now(), cfg=cfg)
    except WaypointError as exc:
        _fail(exc)
    typer.echo(f"Sync {progress.state}: {progress.message}")
    for key, count in sorted(progress.counts.items()):
        typer.echo(f"  {key}: {count}")


@app.command()
def doctor(directory: Path = typer.Option(None, "--dir", help="Project directory")) -> None:
    """Validate config, credentials, connectivity, and the board type."""
    from waypoint.doctor import run_checks

    checks = run_checks(directory or Path.cwd())
    for check in checks:
        marker = "ok  " if check.ok else "FAIL"
        typer.echo(f"{marker} {check.name}: {check.detail}")
    if any(not check.ok for check in checks):
        raise typer.Exit(code=1)


def main() -> None:
    app()
