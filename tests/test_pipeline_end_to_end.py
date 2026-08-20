"""One test that runs the whole pipeline: connectors -> raw -> build -> page.

Every other test in this suite starts halfway. `tests/factories.py` writes the
index directly, so `connector -> build` and `build -> metric` are each asserted
while the *join* between them never is: a field the connector stops emitting, or
a config key `build` never consults, looks fine from both sides. Task 30's
`bot_logins` gap survived thirty task reviews inside exactly that seam.

So this replays the checked-in fixtures through the real `GithubSource` and
`JiraSource` (over `httpx.MockTransport`, so nothing touches the network -- §16),
through the real `RawStore`, through the real `build`, and asserts on figures
rendered by the real routes.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from waypoint.config import load_config
from waypoint.sources.github import GithubSource
from waypoint.sources.http import HttpClient
from waypoint.sources.jira import JiraSource
from waypoint.store.index import connect
from waypoint.sync import run_sync
from waypoint.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)

EMPTY_REPO = {
    "data": {
        "repository": {
            "nameWithOwner": "platform/web",
            "url": "https://ghe.corp.example.com/platform/web",
            "pullRequests": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []},
        }
    }
}


def fixture(*parts: str) -> dict:
    return json.loads((FIXTURES.joinpath(*parts)).read_text())


def github_handler(request: httpx.Request) -> httpx.Response:
    variables = json.loads(request.content)["variables"]
    if variables["name"] != "api":
        return httpx.Response(200, json=EMPTY_REPO)
    page = "graphql_page2.json" if variables["cursor"] else "graphql_page1.json"
    return httpx.Response(200, json=fixture("github", page))


def jira_handler(request: httpx.Request) -> httpx.Response:
    if "/rest/api/3/search" in request.url.path:
        return httpx.Response(200, json=fixture("jira", "search_page1.json"))
    if request.url.path.endswith("/board/42/configuration"):
        return httpx.Response(200, json=fixture("jira", "board_configuration.json"))
    return httpx.Response(404, text="unexpected " + request.url.path)


def client_for(handler, base_url: str) -> HttpClient:
    return HttpClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url),
        sleep=lambda _: None,
    )


def full_sync(project_dir: Path):
    cfg = load_config(project_dir / ".waypoint")
    sources = [
        GithubSource(cfg.github, "tok",
                     http=client_for(github_handler, "https://ghe.corp.example.com")),
        JiraSource(cfg.jira, "em@example.com", "jtok",
                   http=client_for(jira_handler, "https://example.atlassian.net")),
    ]
    return run_sync(project_dir, now=NOW, sources=sources, cfg=cfg)


def test_fixtures_replay_through_the_real_pipeline_onto_a_rendered_page(project_dir: Path):
    progress = full_sync(project_dir)
    assert progress.state == "done"

    root = project_dir / ".waypoint"
    con = connect(root / "index.db", read_only=True)

    # Every entity arrived, so no panel is demoted.
    assert con.execute("SELECT count(*) FROM pull_requests").fetchone()[0] == 3
    assert con.execute("SELECT count(*) FROM jira_issues").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM board_columns").fetchone()[0] == 4

    # The identity join: fixture logins resolve through config.toml's [[people]].
    assert con.execute(
        "SELECT author_person_id FROM pull_requests WHERE number = 482"
    ).fetchone()[0] == "alex-rivera"
    assert con.execute(
        "SELECT assignee_person_id FROM jira_issues WHERE key = 'PROJ-97'"
    ).fetchone()[0] == "alex-rivera"

    body = TestClient(create_app(project_dir)).get("/").text
    assert "demoted" not in body

    # A figure that only exists if the whole chain held: PROJ-97 is In Progress
    # (Jira status id 10002) and the board fixture caps that column at 4.
    assert "In Progress" in body
    assert "1 / 4" in body
    # PROJ-98 resolved 2026-08-12, inside the 14-day window ending at NOW.
    assert "done in last 14d" in body
    # PR #482 has an outstanding review request and no review.
    assert "482" in body
    assert "has had no review for" in body


def test_a_configured_bot_never_reaches_the_sync_page_as_an_unrostered_identity(
    project_dir: Path,
):
    """`graphql_page1.json` has PR #481 authored by `dependabot`, which
    conftest's config.toml lists in `github.bot_logins`. The unattributed
    recorder had no bot filter, so the Sync page told the user to add an
    account that was already there -- the exact defect this end-to-end seam
    exists to catch."""
    full_sync(project_dir)
    con = connect(project_dir / ".waypoint" / "index.db", read_only=True)
    assert con.execute("SELECT count(*) FROM unattributed").fetchone()[0] == 0

    body = TestClient(create_app(project_dir)).get("/sync").text
    assert "dependabot" not in body
    assert "Every identity in the data is in the roster." in body
    assert "UNMATCHED" not in body
