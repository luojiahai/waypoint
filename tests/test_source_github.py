import json
from pathlib import Path

import httpx
import pytest

from waypoint.config import GithubConfig
from waypoint.sources.github import GithubSource
from waypoint.sources.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "github"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_source(handler, repos=("platform/api",), **kwargs) -> GithubSource:
    client = HttpClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://ghe.corp.example.com"),
        sleep=lambda _: None,
    )
    cfg = GithubConfig(
        base_url="https://ghe.corp.example.com", repos=tuple(repos), bot_logins=("dependabot",)
    )
    return GithubSource(cfg, token="tok", http=client, **kwargs)


def paged_handler(pages):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=pages[len(calls) - 1])

    handler.calls = calls
    return handler


def test_emits_one_record_per_pull_request():
    source = make_source(paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")]))
    records = list(source.fetch({}))
    prs = [r for r in records if r.entity == "pull_requests"]
    assert [r.id for r in prs] == [
        "platform/api#482",
        "platform/api#481",
        "platform/api#400",
    ]
    assert prs[0].source == "github"
    assert prs[0].payload["title"] == "PROJ-97 checkout rework"


def test_payload_is_the_untouched_node():
    source = make_source(paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")]))
    pr = next(r for r in source.fetch({}) if r.id == "platform/api#482")
    expected = fixture("graphql_page1.json")["data"]["repository"]["pullRequests"]["nodes"][0]
    assert pr.payload == expected


def test_reviews_are_emitted_as_their_own_records():
    source = make_source(paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")]))
    reviews = [r for r in source.fetch({}) if r.entity == "reviews"]
    assert [r.id for r in reviews] == ["platform/api#481:review:RV_1"]
    assert reviews[0].payload["state"] == "APPROVED"
    assert reviews[0].payload["pull_request_id"] == "platform/api#481"


def test_review_requests_come_from_timeline_events_and_carry_requested_at():
    source = make_source(paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")]))
    requests = [r for r in source.fetch({}) if r.entity == "review_requests"]
    assert requests[0].id == "platform/api#482:requested:bchen:2026-08-14T10:05:00Z"
    assert requests[0].payload == {
        "pull_request_id": "platform/api#482",
        "login": "bchen",
        "requested_at": "2026-08-14T10:05:00Z",
    }


def test_ready_for_review_event_is_preserved_in_the_pr_payload():
    source = make_source(paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")]))
    pr = next(r for r in source.fetch({}) if r.id == "platform/api#481")
    kinds = [n["__typename"] for n in pr.payload["timelineItems"]["nodes"]]
    assert "ReadyForReviewEvent" in kinds


def test_paging_stops_once_updated_at_falls_below_the_watermark():
    handler = paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")])
    source = make_source(handler)
    list(source.fetch({"pull_requests": "2026-08-13T00:00:00Z"}))
    assert len(handler.calls) == 1


def test_incremental_fetch_drops_records_older_than_the_watermark():
    handler = paged_handler([fixture("graphql_page1.json")])
    source = make_source(handler)
    ids = [r.id for r in source.fetch({"pull_requests": "2026-08-13T00:00:00Z"}) if r.entity == "pull_requests"]
    assert ids == ["platform/api#482"]


def test_watermark_is_the_newest_updated_at_seen():
    source = make_source(paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")]))
    list(source.fetch({}))
    assert source.status()["pull_requests"].watermark == "2026-08-18T12:00:00Z"


def test_status_is_ok_with_counts_after_a_clean_run():
    source = make_source(paged_handler([fixture("graphql_page1.json"), fixture("graphql_page2.json")]))
    list(source.fetch({}))
    status = source.status()
    assert status["pull_requests"].status == "ok"
    assert status["pull_requests"].count == 3
    assert status["reviews"].count == 1


def test_a_failing_repo_marks_partial_and_the_other_repo_still_returns():
    def handler(request):
        body = json.loads(request.content)
        if body["variables"]["name"] == "api":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=fixture("graphql_page2.json"))

    source = make_source(handler, repos=("platform/api", "platform/web"))
    records = list(source.fetch({}))
    assert any(r.id.endswith("#400") for r in records)
    status = source.status()
    assert status["pull_requests"].status == "partial"
    assert "platform/api" in status["pull_requests"].error


def test_every_repo_failing_marks_failed():
    source = make_source(lambda request: httpx.Response(500, text="boom"))
    assert list(source.fetch({})) == []
    assert source.status()["pull_requests"].status == "failed"


def test_graphql_errors_in_a_200_response_are_treated_as_failure():
    source = make_source(
        lambda request: httpx.Response(200, json={"errors": [{"message": "Field 'x' doesn't exist"}]})
    )
    assert list(source.fetch({})) == []
    assert "doesn't exist" in source.status()["pull_requests"].error


def test_request_sends_a_bearer_token_and_never_logs_it():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=fixture("graphql_page2.json"))

    source = make_source(handler)
    list(source.fetch({}))
    assert seen["auth"] == "Bearer tok"
    assert "tok" not in json.dumps({k: v.__dict__ for k, v in source.status().items()})
