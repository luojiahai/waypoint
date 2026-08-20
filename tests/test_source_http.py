import httpx
import pytest

from waypoint.errors import SourceError
from waypoint.sources.http import HttpClient, classify


def make_client(handler, **kwargs) -> tuple[HttpClient, list[float]]:
    slept: list[float] = []
    transport = httpx.MockTransport(handler)
    client = HttpClient(
        httpx.Client(transport=transport, base_url="https://api.test"),
        sleep=slept.append,
        backoff_base=1.0,
        **kwargs,
    )
    return client, slept


def test_successful_request_returns_the_response():
    client, _ = make_client(lambda request: httpx.Response(200, json={"ok": True}))
    assert client.get("/thing").json() == {"ok": True}


def test_retries_on_429_and_honours_retry_after_seconds():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    client, slept = make_client(handler)
    assert client.get("/thing").status_code == 200
    assert slept == [7.0, 7.0]
    assert client.rate_limit.waited_seconds == 14.0


def test_retries_on_502_with_exponential_backoff_when_no_retry_after():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(502) if len(calls) < 3 else httpx.Response(200)

    client, slept = make_client(handler)
    assert client.get("/thing").status_code == 200
    assert slept == [1.0, 2.0]


def test_retries_on_transport_error():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 2:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200)

    client, slept = make_client(handler)
    assert client.get("/thing").status_code == 200
    assert slept == [1.0]


def test_exhausted_retries_raise_a_rate_limit_error_naming_the_wait():
    client, _ = make_client(
        lambda request: httpx.Response(429, headers={"Retry-After": "3"}), max_attempts=2
    )
    with pytest.raises(SourceError) as exc:
        client.get("/thing")
    assert exc.value.kind == "rate_limit"
    assert "3" in exc.value.message


def test_401_is_not_retried_and_names_the_token_variable():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"message": "Bad credentials"})

    client, _ = make_client(handler)
    with pytest.raises(SourceError) as exc:
        client.get("/thing")
    assert exc.value.kind == "auth"
    assert len(calls) == 1


def test_403_rate_limit_and_403_scope_are_distinguished():
    limited = httpx.Response(
        403, headers={"X-RateLimit-Remaining": "0"}, json={"message": "API rate limit exceeded"}
    )
    scoped = httpx.Response(403, json={"message": "Resource not accessible by personal access token"})
    assert classify(limited).kind == "rate_limit"
    assert classify(scoped).kind == "scope"


def test_rate_limit_headers_are_captured_for_the_sync_page():
    client, _ = make_client(
        lambda request: httpx.Response(
            200, headers={"X-RateLimit-Remaining": "412", "X-RateLimit-Reset": "1787200000"}
        )
    )
    client.get("/thing")
    assert client.rate_limit.remaining == 412
    assert client.rate_limit.reset_at == "2026-08-20T04:26:40Z"


def test_error_messages_never_echo_the_authorization_header():
    def handler(request):
        return httpx.Response(401, text=f"denied {request.headers.get('authorization')}")

    client, _ = make_client(handler)
    client.client.headers["Authorization"] = "Bearer super-secret"
    with pytest.raises(SourceError) as exc:
        client.get("/thing")
    assert "super-secret" not in exc.value.message


def test_negative_retry_after_does_not_crash_and_records_a_non_negative_sleep():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 2:
            return httpx.Response(429, headers={"Retry-After": "-5"})
        return httpx.Response(200, json={"ok": True})

    client, slept = make_client(handler)
    assert client.get("/thing").status_code == 200
    assert slept == [0.0]
    assert client.rate_limit.waited_seconds == 0.0


def test_a_zero_retry_after_still_names_the_suggested_wait():
    """`Retry-After: 0` is a real instruction, not an absent one -- reading it
    for truthiness dropped the sentence the `is not None` check above keeps."""
    client, _ = make_client(
        lambda request: httpx.Response(429, headers={"Retry-After": "0"}), max_attempts=1
    )
    with pytest.raises(SourceError) as exc:
        client.get("/thing")
    assert "Last suggested wait was 0s." in exc.value.message


def test_a_two_hundred_that_is_not_json_becomes_an_actionable_source_error():
    """A 200 carrying an SSO login page would otherwise raise JSONDecodeError,
    which `run_sync` does not catch -- so the CLI prints a traceback and
    `progress.json` is left saying "running" forever."""
    from waypoint.sources.http import json_body

    client, _ = make_client(
        lambda request: httpx.Response(200, text="<html>Sign in to continue</html>")
    )
    with pytest.raises(SourceError) as exc:
        json_body(client.get("/thing"), "The GitHub GraphQL API")
    message = exc.value.message
    assert "The GitHub GraphQL API" in message
    assert "not JSON" in message
    assert "config.toml" in message and "waypoint doctor" in message
    assert exc.value.kind == "http"
