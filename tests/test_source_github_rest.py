import json
from pathlib import Path

import httpx
import pytest

from waypoint.config import GithubConfig
from waypoint.errors import SourceError
from waypoint.sources.github import GithubSource
from waypoint.sources.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "github"


def fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


def make_source(handler, **kwargs) -> GithubSource:
    client = HttpClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://ghe.corp.example.com"),
        sleep=lambda _: None,
    )
    cfg = GithubConfig(
        base_url="https://ghe.corp.example.com", repos=("platform/api",), bot_logins=("dependabot",)
    )
    return GithubSource(cfg, token="tok", http=client, **kwargs)


def rest_handler(request):
    path = request.url.path
    if path.endswith("/pulls"):
        return httpx.Response(200, json=fixture("rest_pulls.json"))
    if path.endswith("/pulls/482/reviews"):
        return httpx.Response(200, json=fixture("rest_reviews_482.json"))
    return httpx.Response(200, json=[])


def test_rest_fallback_emits_the_same_record_ids():
    source = make_source(rest_handler, use_graphql=False)
    records = list(source.fetch({}))
    assert [r.id for r in records if r.entity == "pull_requests"] == ["platform/api#482"]
    assert [r.id for r in records if r.entity == "reviews"] == ["platform/api#482:review:90001"]


def test_rest_review_requests_do_not_fabricate_a_requested_at():
    # REST exposes no per-request timestamp. Substituting the PR's created_at
    # would invent a plausible-looking but wrong review-wait time, so the
    # connector says "unknown" rather than guessing.
    source = make_source(rest_handler, use_graphql=False)
    requests = [r for r in source.fetch({}) if r.entity == "review_requests"]
    user_request = next(r for r in requests if r.payload["login"] == "bchen")
    assert user_request.id == "platform/api#482:requested:bchen:unknown"
    assert user_request.payload == {
        "pull_request_id": "platform/api#482",
        "login": "bchen",
        "requested_at": "unknown",
    }


def test_rest_review_request_from_a_team_still_produces_a_record():
    source = make_source(rest_handler, use_graphql=False)
    requests = [r for r in source.fetch({}) if r.entity == "review_requests"]
    team_requests = [r for r in requests if r.payload["login"].startswith("team:")]
    assert team_requests[0].id == "platform/api#482:requested:team:platform-reviewers:unknown"
    assert team_requests[0].payload == {
        "pull_request_id": "platform/api#482",
        "login": "team:platform-reviewers",
        "requested_at": "unknown",
    }


def test_rest_pages_until_a_short_page():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/pulls"):
            page = request.url.params.get("page")
            return httpx.Response(200, json=fixture("rest_pulls.json") if page == "1" else [])
        return httpx.Response(200, json=[])

    source = make_source(handler, use_graphql=False, page_size=1)
    list(source.fetch({}))
    assert sum(1 for url in calls if "/pulls?" in url) == 2


def test_rest_stops_at_the_watermark():
    source = make_source(rest_handler, use_graphql=False)
    records = list(source.fetch({"pull_requests": "2026-08-19T00:00:00Z"}))
    assert [r for r in records if r.entity == "pull_requests"] == []


def test_probe_graphql_returns_true_when_the_query_is_accepted():
    source = make_source(lambda request: httpx.Response(200, json={"data": {"repository": None}}))
    assert source.probe_graphql() is True


def test_probe_graphql_returns_false_on_a_field_error():
    source = make_source(
        lambda request: httpx.Response(
            200, json={"errors": [{"message": "Field 'timelineItems' doesn't exist"}]}
        )
    )
    assert source.probe_graphql() is False


def test_probe_graphql_returns_false_when_graphql_is_absent():
    source = make_source(lambda request: httpx.Response(404, text="not found"))
    assert source.probe_graphql() is False


def test_probe_graphql_reraises_on_auth_failure_instead_of_returning_false():
    # A 401 means "fix your token", not "this GHE instance lacks GraphQL" — the
    # two must not collapse to the same False, or `waypoint doctor` would tell
    # a user with a bad token to fall back to REST instead of fixing auth.
    source = make_source(lambda request: httpx.Response(401, text="Bad credentials"))
    with pytest.raises(SourceError) as excinfo:
        source.probe_graphql()
    assert excinfo.value.kind == "auth"


def test_reachable_returns_true_on_a_successful_request():
    source = make_source(lambda request: httpx.Response(200, json={"login": "svc-account"}))
    assert source.reachable() is True


def test_reachable_raises_on_auth_failure():
    # `waypoint doctor` reports this as a named failure, not a crash — the
    # message must survive intact for the CLI to show it.
    source = make_source(lambda request: httpx.Response(401, text="Bad credentials"))
    with pytest.raises(SourceError) as excinfo:
        source.reachable()
    assert excinfo.value.kind == "auth"


def test_repo_readable_returns_true_when_the_repo_resolves():
    source = make_source(lambda request: httpx.Response(200, json={"full_name": "platform/api"}))
    assert source.repo_readable("platform/api") is True


def test_repo_readable_raises_not_found_for_a_missing_repo():
    source = make_source(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(SourceError) as excinfo:
        source.repo_readable("platform/ghost")
    assert excinfo.value.kind == "not_found"
