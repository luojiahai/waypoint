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


@app.command()
def query(
    sql: str,
    directory: Path = typer.Option(None, "--dir", help="Project directory"),
    output: str = typer.Option("json", "--format", help="json or table"),
) -> None:
    """Read-only query against the index. This is how skills read data."""
    import json as json_module
    import sqlite3

    project = directory or Path.cwd()
    database = _root(project) / "index.db"
    if not database.exists():
        typer.echo("No index yet. Run `waypoint sync` first.")
        raise typer.Exit(code=1)

    stripped = sql.strip().rstrip(";")
    if ";" in stripped or not stripped.lower().startswith(("select", "with")):
        typer.echo("`waypoint query` is read-only: one SELECT statement, no semicolons.")
        raise typer.Exit(code=1)

    con = index_store.connect(database, read_only=True)
    try:
        rows = [dict(row) for row in con.execute(stripped)]
    except sqlite3.Error as exc:
        typer.echo(f"SQL error: {exc}")
        raise typer.Exit(code=1)
    finally:
        con.close()

    if output == "table":
        if rows:
            headers = list(rows[0])
            typer.echo("  ".join(headers))
            for row in rows:
                typer.echo("  ".join(str(row[key]) for key in headers))
    else:
        typer.echo(json_module.dumps(rows, indent=2, default=str))


@app.command("capture-fixtures")
def capture_fixtures(
    out: Path = typer.Option(Path("tests/fixtures"), "--out"),
    directory: Path = typer.Option(None, "--dir", help="Project directory"),
    limit: int = typer.Option(5, "--limit"),
) -> None:
    """Capture redacted raw payloads from a live instance for operators to inspect.

    Not the source of the connector tests' fixtures — those are hand-written
    per-API-page JSON checked in under tests/fixtures/{github,jira}. This
    writes per-entity JSONL snapshots instead, for grabbing real, redacted
    data to look at, not for replay in the connector test suite.
    """
    from waypoint.fixtures import capture

    try:
        written = capture(directory or Path.cwd(), out, limit=limit)
    except WaypointError as exc:
        _fail(exc)
    for path in written:
        typer.echo(f"wrote {path}")


@app.command()
def serve(
    directory: Path = typer.Option(None, "--dir", help="Project directory"),
    port: int = typer.Option(8787, "--port", help="Port to listen on (default 8787)"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Start the local web app."""
    import webbrowser

    import uvicorn

    from waypoint.web.app import create_app

    project = directory or Path.cwd()
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    uvicorn.run(create_app(project), host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    app()
