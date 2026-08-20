import base64
import json
from pathlib import Path

import httpx
import pytest

from waypoint.config import JiraConfig
from waypoint.errors import SourceError
from waypoint.sources.http import HttpClient
from waypoint.sources.jira import JiraSource

FIXTURES = Path(__file__).parent / "fixtures" / "jira"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_source(handler, **kwargs) -> JiraSource:
    client = HttpClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.atlassian.net"),
        sleep=lambda _: None,
    )
    cfg = JiraConfig(
        site="example.atlassian.net",
        project_key="PROJ",
        board_id=42,
        story_points_field="customfield_10016",
    )
    return JiraSource(cfg, email="em@example.com", token="jtok", http=client, **kwargs)


def default_handler(request):
    if "/rest/api/3/search" in request.url.path:
        return httpx.Response(200, json=fixture("search_page1.json"))
    if request.url.path.endswith("/board/42/configuration"):
        return httpx.Response(200, json=fixture("board_configuration.json"))
    if request.url.path.endswith("/board/42"):
        return httpx.Response(200, json=fixture("board.json"))
    return httpx.Response(404, text="unexpected " + request.url.path)


def test_emits_one_issue_record_per_issue():
    source = make_source(default_handler)
    issues = [r for r in source.fetch({}) if r.entity == "issues"]
    assert [r.id for r in issues] == ["PROJ-97", "PROJ-98"]
    assert issues[0].payload["fields"]["summary"] == "Checkout rework"


def test_issue_status_carries_the_board_column_id():
    # board_configuration.json maps "In Progress" -> 10002 and "Done" -> 10004.
    # If status.id were ever stripped, every board column would silently empty
    # out downstream (Tasks 16/19/24/25 join on jira_issues.status_id).
    source = make_source(default_handler)
    issues = [r for r in source.fetch({}) if r.entity == "issues"]
    assert issues[0].payload["fields"]["status"]["id"] == "10002"
    assert issues[1].payload["fields"]["status"]["id"] == "10004"


def test_changelog_is_split_into_its_own_record():
    source = make_source(default_handler)
    changelogs = [r for r in source.fetch({}) if r.entity == "changelogs"]
    assert [r.id for r in changelogs] == ["PROJ-97:changelog", "PROJ-98:changelog"]
    assert changelogs[0].payload["histories"][0]["items"][0]["toString"] == "In Progress"


def test_issue_payload_retains_the_changelog_too():
    source = make_source(default_handler)
    issue = next(r for r in source.fetch({}) if r.id == "PROJ-97")
    assert "changelog" in issue.payload


def test_search_requests_expand_changelog():
    seen = {}

    def handler(request):
        if "/search" in request.url.path:
            seen["params"] = dict(request.url.params)
        return default_handler(request)

    list(make_source(handler).fetch({}))
    assert seen["params"]["expand"] == "changelog"


def test_jql_filters_by_project_and_watermark():
    seen = {}

    def handler(request):
        if "/search" in request.url.path:
            seen["jql"] = request.url.params.get("jql")
        return default_handler(request)

    list(make_source(handler).fetch({"issues": "2026-08-01T00:00:00Z"}))
    assert seen["jql"] == 'project = "PROJ" AND updated >= "2026-08-01 00:00" ORDER BY updated ASC'


def test_first_sync_uses_project_only_jql():
    seen = {}

    def handler(request):
        if "/search" in request.url.path:
            seen["jql"] = request.url.params.get("jql")
        return default_handler(request)

    list(make_source(handler).fetch({}))
    assert seen["jql"] == 'project = "PROJ" ORDER BY updated ASC'


def test_board_configuration_is_fetched_every_sync():
    source = make_source(default_handler)
    configs = [r for r in source.fetch({}) if r.entity == "board_config"]
    assert [r.id for r in configs] == ["board:42"]
    assert configs[0].payload["columnConfig"]["columns"][1]["max"] == 4


def test_sprints_are_never_requested():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return default_handler(request)

    list(make_source(handler).fetch({}))
    assert not any("sprint" in path for path in seen)


def test_watermark_is_the_newest_updated_field():
    source = make_source(default_handler)
    list(source.fetch({}))
    assert source.status()["issues"].watermark == "2026-08-18T12:00:00Z"


def test_paging_follows_start_at_until_total_is_reached():
    calls = []

    def handler(request):
        if "/search" in request.url.path:
            calls.append(int(request.url.params.get("startAt")))
            page = fixture("search_page1.json")
            page["total"] = 4
            page["maxResults"] = 2
            if calls[-1] >= 2:
                page["issues"] = []
            return httpx.Response(200, json=page)
        return default_handler(request)

    list(make_source(handler, page_size=2).fetch({}))
    assert calls == [0, 2]


def test_issue_failure_still_lets_board_config_through():
    def handler(request):
        if "/search" in request.url.path:
            return httpx.Response(500, text="boom")
        return default_handler(request)

    source = make_source(handler)
    records = list(source.fetch({}))
    assert [r.entity for r in records] == ["board_config"]
    status = source.status()
    assert status["issues"].status == "failed"
    assert status["changelogs"].status == "failed"
    assert status["board_config"].status == "ok"


def test_board_config_failure_does_not_discard_issues():
    def handler(request):
        if request.url.path.endswith("/configuration"):
            return httpx.Response(403, json={"message": "no access"})
        return default_handler(request)

    source = make_source(handler)
    records = list(source.fetch({}))
    assert any(r.entity == "issues" for r in records)
    assert source.status()["board_config"].status == "failed"
    assert source.status()["issues"].status == "ok"


def test_uses_http_basic_auth_with_email_and_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return default_handler(request)

    list(make_source(handler).fetch({}))
    expected = base64.b64encode(b"em@example.com:jtok").decode()
    assert seen["auth"] == f"Basic {expected}"


def test_board_type_reads_the_board_resource():
    assert make_source(default_handler).board_type() == "kanban"


def test_untruncated_changelog_leaves_changelogs_status_ok():
    source = make_source(default_handler)
    list(source.fetch({}))
    assert source.status()["changelogs"].status == "ok"


def test_truncated_changelog_marks_changelogs_partial_with_issue_key():
    def handler(request):
        if "/search" in request.url.path:
            return httpx.Response(200, json=fixture("search_truncated_changelog.json"))
        return default_handler(request)

    source = make_source(handler)
    records = list(source.fetch({}))

    # The truncated changelog is still yielded in full — activity is never dropped.
    changelogs = [r for r in records if r.entity == "changelogs"]
    assert [r.id for r in changelogs] == ["PROJ-99:changelog"]
    assert len(changelogs[0].payload["histories"]) == 1

    status = source.status()["changelogs"]
    assert status.status == "partial"
    assert "PROJ-99" in status.error


def test_reachable_returns_true_on_a_successful_request():
    source = make_source(lambda request: httpx.Response(200, json={"accountId": "acct-1"}))
    assert source.reachable() is True


def test_reachable_raises_on_auth_failure():
    source = make_source(lambda request: httpx.Response(401, text="Unauthorized"))
    with pytest.raises(SourceError) as excinfo:
        source.reachable()
    assert excinfo.value.kind == "auth"


def test_board_configuration_readable_returns_true_when_the_endpoint_resolves():
    assert make_source(default_handler).board_configuration_readable() is True


def test_board_configuration_readable_raises_on_a_missing_board():
    source = make_source(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(SourceError) as excinfo:
        source.board_configuration_readable()
    assert excinfo.value.kind == "not_found"


def test_a_non_json_two_hundred_is_recorded_as_a_failure_not_raised_raw():
    """Same guard as the GitHub connector: a 200 that is not JSON must arrive
    as a `SourceError` so the entity is marked failed with a message the user
    can act on, rather than escaping `run_sync` as a `JSONDecodeError`."""
    source = make_source(lambda request: httpx.Response(200, text="<html>Sign in</html>"))
    assert list(source.fetch({})) == []
    for entity in ("issues", "board_config"):
        status = source.status()[entity]
        assert status.status == "failed"
        assert "not JSON" in status.error
        assert "waypoint doctor" in status.error
