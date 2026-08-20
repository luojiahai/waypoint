from pathlib import Path

import httpx
import pytest

from waypoint.doctor import Check, run_checks


class StubGithub:
    def __init__(self, graphql=True, reachable=True, unreadable_repos=(), graphql_auth_error=False):
        self._graphql = graphql
        self._reachable = reachable
        self._unreadable_repos = set(unreadable_repos)
        self._graphql_auth_error = graphql_auth_error
        self.name = "github"

    def probe_graphql(self):
        if self._graphql_auth_error:
            from waypoint.errors import SourceError

            raise SourceError(
                "Authentication failed for https://ghe.corp.example.com/api/graphql. Check "
                "WAYPOINT_GITHUB_TOKEN / WAYPOINT_JIRA_EMAIL / WAYPOINT_JIRA_TOKEN, then run "
                "`waypoint doctor`.",
                kind="auth",
            )
        return self._graphql

    def reachable(self):
        # A real GithubSource.reachable() only ever returns True or raises
        # SourceError (it never returns False) — this stub models the same
        # contract so the doctor tests exercise a path production can take.
        if not self._reachable:
            from waypoint.errors import SourceError

            raise SourceError(
                "Could not reach https://ghe.corp.example.com/api/v3/user: "
                "Connection refused",
                kind="server",
            )
        return True

    def repo_readable(self, repo):
        if repo in self._unreadable_repos:
            from waypoint.errors import SourceError

            raise SourceError(
                f"Not found: {repo}. Check github.repos in config.toml.", kind="not_found"
            )
        return True


class StubJira:
    def __init__(self, board="kanban", reachable=True):
        self._board = board
        self._reachable = reachable
        self.name = "jira"

    def board_type(self):
        if not self._reachable:
            from waypoint.errors import SourceError

            raise SourceError("connection refused", kind="server")
        return self._board

    def reachable(self):
        # A real JiraSource.reachable() only ever returns True or raises
        # SourceError (it never returns False) — this stub models the same
        # contract so the doctor tests exercise a path production can take.
        if not self._reachable:
            from waypoint.errors import SourceError

            raise SourceError(
                "Could not reach https://example.atlassian.net/rest/api/3/myself: "
                "Connection refused",
                kind="server",
            )
        return True

    def board_configuration_readable(self):
        if not self._reachable:
            from waypoint.errors import SourceError

            raise SourceError("connection refused", kind="server")
        return True


def results(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


def test_all_green_on_a_healthy_setup(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    checks = run_checks(project_dir, sources=(StubGithub(), StubJira()))
    assert all(check.ok for check in checks), [c for c in checks if not c.ok]


def test_checks_are_emitted_in_the_documented_order(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    checks = run_checks(project_dir, sources=(StubGithub(), StubJira()))
    assert [c.name for c in checks] == [
        "config",
        "secrets",
        "roster",
        "github",
        "graphql",
        "repos",
        "jira",
        "board",
        "board_config",
    ]


def test_missing_config_fails_the_first_check_and_stops(tmp_path: Path):
    checks = run_checks(tmp_path, sources=(StubGithub(), StubJira()))
    assert checks[0].name == "config"
    assert checks[0].ok is False
    assert len(checks) == 1


def test_missing_secrets_are_named(project_dir: Path, monkeypatch):
    for name in ("WAYPOINT_GITHUB_TOKEN", "WAYPOINT_JIRA_EMAIL", "WAYPOINT_JIRA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    check = results(run_checks(project_dir, sources=(StubGithub(), StubJira())))["secrets"]
    assert check.ok is False
    assert "WAYPOINT_JIRA_TOKEN" in check.detail


def test_incomplete_roster_names_the_person_and_the_missing_field(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    root = project_dir / ".waypoint"
    root.joinpath("config.toml").write_text(
        root.joinpath("config.toml").read_text().replace('github_login = "bchen"', 'github_login = ""')
    )
    check = results(run_checks(project_dir, sources=(StubGithub(), StubJira())))["roster"]
    assert check.ok is False
    assert "Bo Chen" in check.detail
    assert "github_login" in check.detail


def test_a_non_kanban_board_fails_with_a_clear_message(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    check = results(run_checks(project_dir, sources=(StubGithub(), StubJira(board="scrum"))))["board"]
    assert check.ok is False
    assert "kanban" in check.detail
    assert "scrum" in check.detail


def test_graphql_unavailable_is_a_warning_not_a_failure(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    check = results(run_checks(project_dir, sources=(StubGithub(graphql=False), StubJira())))["graphql"]
    assert check.ok is True
    assert "REST" in check.detail


def test_graphql_auth_failure_is_reported_and_not_collapsed_to_unavailable(project_dir: Path, monkeypatch):
    # A 401 means "fix your token", not "this GHE instance lacks GraphQL" (Task 8).
    # probe_graphql() re-raises SourceError(kind="auth") for exactly this reason;
    # doctor must surface that distinction, not fold it into the "unavailable,
    # will use REST" warning.
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    check = results(
        run_checks(project_dir, sources=(StubGithub(graphql_auth_error=True), StubJira()))
    )["graphql"]
    assert check.ok is False
    assert "WAYPOINT_GITHUB_TOKEN" in check.detail
    assert "unavailable" not in check.detail


def test_an_unreachable_jira_is_reported_without_a_traceback(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    check = results(run_checks(project_dir, sources=(StubGithub(), StubJira(reachable=False))))["board"]
    assert check.ok is False
    assert "connection refused" in check.detail


def test_github_unreachable_is_named_and_actionable(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    check = results(run_checks(project_dir, sources=(StubGithub(reachable=False), StubJira())))["github"]
    assert check.ok is False
    assert "ghe.corp.example.com" in check.detail


def test_an_unreadable_repo_is_named_by_repo(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    sources = (StubGithub(unreadable_repos=("platform/web",)), StubJira())
    check = results(run_checks(project_dir, sources=sources))["repos"]
    assert check.ok is False
    assert "platform/web" in check.detail


def test_jira_unreachable_is_named_and_actionable(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    check = results(run_checks(project_dir, sources=(StubGithub(), StubJira(reachable=False))))["jira"]
    assert check.ok is False
    assert "example.atlassian.net" in check.detail


def test_unreadable_board_configuration_is_reported_without_a_traceback(project_dir: Path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    check = results(
        run_checks(project_dir, sources=(StubGithub(), StubJira(reachable=False)))
    )["board_config"]
    assert check.ok is False
    assert "connection refused" in check.detail
