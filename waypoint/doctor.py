"""Validate everything before the first sync.

The common first-run failure should be a clear message, not a stack trace
mid-backfill (§15).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from waypoint.config import load_config, load_secrets
from waypoint.errors import SourceError, WaypointError
from waypoint.roster import Roster
from waypoint.sources.github import GithubSource
from waypoint.sources.http import HttpClient
from waypoint.sources.jira import JiraSource


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _default_sources(project_dir: Path, cfg, secrets):
    github = GithubSource(
        cfg.github, secrets.github_token, http=HttpClient(httpx.Client(timeout=15.0))
    )
    jira = JiraSource(
        cfg.jira, secrets.jira_email, secrets.jira_token,
        http=HttpClient(httpx.Client(timeout=15.0)),
    )
    return github, jira


def run_checks(project_dir: Path, *, sources: tuple | None = None) -> list[Check]:
    project_dir = Path(project_dir)
    checks: list[Check] = []

    try:
        cfg = load_config(project_dir / ".waypoint")
    except WaypointError as exc:
        return [Check("config", False, exc.message)]
    checks.append(Check("config", True, f"{len(cfg.github.repos)} repos, board {cfg.jira.board_id}"))

    secrets = load_secrets(project_dir)
    missing = secrets.missing()
    checks.append(
        Check("secrets", not missing, "all present" if not missing else "missing " + ", ".join(missing))
    )

    roster = Roster.from_config(cfg)
    gaps = [
        f"{person.name} has no {field}"
        for person in roster.active_people()
        for field, value in (("github_login", person.github_login),
                             ("jira_account_id", person.jira_account_id))
        if not value
    ]
    checks.append(
        Check("roster", not gaps, "; ".join(gaps) if gaps
              else f"{len(roster.active_people())} active people, all identities set")
    )

    github, jira = sources or _default_sources(project_dir, cfg, secrets)

    try:
        github.reachable()
        checks.append(Check("github", True, "reachable"))
    except SourceError as exc:
        checks.append(Check("github", False, exc.message))

    try:
        graphql = github.probe_graphql()
        checks.append(
            Check(
                "graphql", True,
                "available" if graphql
                else "unavailable on this GHE version — the connector will use REST, "
                     "which is slower but complete",
            )
        )
    except SourceError as exc:
        checks.append(Check("graphql", False, exc.message))

    repo_failures = []
    for repo in cfg.github.repos:
        try:
            github.repo_readable(repo)
        except SourceError as exc:
            repo_failures.append(f"{repo}: {exc.message}")
    checks.append(
        Check(
            "repos", not repo_failures,
            "; ".join(repo_failures) if repo_failures
            else f"{len(cfg.github.repos)} repos readable",
        )
    )

    try:
        jira.reachable()
        checks.append(Check("jira", True, "reachable"))
    except SourceError as exc:
        checks.append(Check("jira", False, exc.message))

    try:
        board_type = jira.board_type()
        if board_type == "kanban":
            checks.append(Check("board", True, f"board {cfg.jira.board_id} is kanban"))
        else:
            checks.append(
                Check(
                    "board", False,
                    f"board {cfg.jira.board_id} is a {board_type} board. Waypoint is "
                    f"kanban-only: point jira.board_id at a kanban board.",
                )
            )
    except SourceError as exc:
        checks.append(Check("board", False, exc.message))

    try:
        jira.board_configuration_readable()
        checks.append(
            Check("board_config", True, f"board {cfg.jira.board_id} configuration readable")
        )
    except SourceError as exc:
        checks.append(Check("board_config", False, exc.message))

    return checks
