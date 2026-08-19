import json
from pathlib import Path

import httpx

from waypoint.config import GithubConfig
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


def test_rest_review_requests_carry_the_pr_created_at_as_requested_at():
    source = make_source(rest_handler, use_graphql=False)
    requests = [r for r in source.fetch({}) if r.entity == "review_requests"]
    assert requests[0].payload == {
        "pull_request_id": "platform/api#482",
        "login": "bchen",
        "requested_at": "2026-08-14T10:00:00Z",
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
