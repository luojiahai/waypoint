"""Shared connector HTTP behaviour: backoff, Retry-After, error classification.

A rate-limit wait reports its duration rather than appearing hung, and 401,
403-scope, and 403-rate-limit produce distinct, actionable messages (§15).
Response bodies are truncated and request headers are never echoed, so a token
cannot reach a log line through an error message.
"""

from __future__ import annotations

import email.utils
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from waypoint.errors import SourceError

RETRY_STATUSES = {429, 502, 503, 504}
_BODY_LIMIT = 300


@dataclass
class RateLimitState:
    remaining: int | None = None
    reset_at: str | None = None
    waited_seconds: float = 0.0


def _body_excerpt(response: httpx.Response) -> str:
    try:
        text = response.text
    except Exception:  # pragma: no cover - body already consumed
        return ""
    return " ".join(text.split())[:_BODY_LIMIT]


def json_body(response: httpx.Response, what: str) -> Any:
    """`response.json()`, but a body that is not JSON becomes a `SourceError`.

    A 200 carrying HTML -- an SSO interstitial, a proxy error page, a base_url
    pointing at the web UI instead of the API -- otherwise raises
    `json.JSONDecodeError`, which is not a `WaypointError`. `run_sync` catches
    only `WaypointError`, so such a body escapes the whole handler: the CLI
    prints a traceback instead of a message, and the final `write_progress`
    never runs, leaving `state/progress.json` stuck at "running" (§15).
    """
    try:
        return response.json()
    except ValueError as exc:
        excerpt = _body_excerpt(response)
        began = f" It began: {excerpt[:80]}." if excerpt else ""
        raise SourceError(
            f"{what} returned {response.status_code} with a body that is not JSON "
            f"({exc}).{began} Check that the base URL in .waypoint/config.toml points "
            "at the API host and is not behind an SSO or proxy login page, then run "
            "`waypoint doctor`.",
            kind="http",
        ) from exc


def classify(response: httpx.Response) -> SourceError:
    """Map a failing response onto an error a user can act on."""
    try:
        url = str(response.request.url)
    except RuntimeError:  # response.request raises when no request is attached
        url = ""
    body = _body_excerpt(response)
    status = response.status_code

    if status == 401:
        return SourceError(
            f"Authentication failed for {url}. Check WAYPOINT_GITHUB_TOKEN / "
            f"WAYPOINT_JIRA_EMAIL / WAYPOINT_JIRA_TOKEN, then run `waypoint doctor`.",
            kind="auth",
        )
    if status == 403:
        rate_limited = (
            response.headers.get("X-RateLimit-Remaining") == "0"
            or "rate limit" in body.casefold()
        )
        if rate_limited:
            return SourceError(
                f"Rate limited by {url}. Waypoint will back off and retry; "
                f"sync again later to complete the remaining pages.",
                kind="rate_limit",
            )
        return SourceError(
            f"Token lacks the scope needed for {url}: {body}", kind="scope"
        )
    if status == 429:
        wait = _retry_after_seconds(response)
        wait_text = f"{wait:.0f}s" if wait is not None else "a few seconds"
        return SourceError(
            f"Rate limited by {url}. Waypoint will wait {wait_text} and retry; "
            f"sync again later to complete the remaining pages.",
            kind="rate_limit",
        )
    if status == 404:
        return SourceError(
            f"Not found: {url}. Check the repo names, jira.project_key, and jira.board_id "
            f"in config.toml.",
            kind="not_found",
        )
    if status >= 500:
        return SourceError(f"{url} returned {status}: {body}", kind="server")
    return SourceError(f"{url} returned {status}: {body}", kind="http")


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


class HttpClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        backoff_base: float = 1.0,
    ) -> None:
        self.client = client
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._backoff_base = backoff_base
        self.rate_limit = RateLimitState()

    def _note_rate_limit(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_limit.remaining = int(remaining)
        reset = response.headers.get("X-RateLimit-Reset")
        if reset is not None and reset.isdigit():
            self.rate_limit.reset_at = (
                datetime.fromtimestamp(int(reset), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            )

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error: SourceError | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_error = SourceError(f"Could not reach {url}: {exc}", kind="server")
                if attempt == self.max_attempts - 1:
                    break
                self._wait(self._backoff_base * (2**attempt))
                continue

            self._note_rate_limit(response)
            if response.status_code < 400:
                return response

            error = classify(response)
            retryable = response.status_code in RETRY_STATUSES or error.kind == "rate_limit"
            if not retryable or attempt == self.max_attempts - 1:
                if retryable:
                    delay = _retry_after_seconds(response)
                    # `is not None`, not truthiness: a legitimate `Retry-After: 0`
                    # is still a suggested wait, and matches the check below.
                    suffix = (
                        f" Last suggested wait was {delay:.0f}s." if delay is not None else ""
                    )
                    raise SourceError(error.message + suffix, kind=error.kind)
                raise error
            delay = _retry_after_seconds(response)
            if delay is None:
                delay = self._backoff_base * (2**attempt)
            last_error = error
            self._wait(delay)

        raise last_error or SourceError(f"Request to {url} failed", kind="http")

    def _wait(self, seconds: float) -> None:
        self.rate_limit.waited_seconds += seconds
        self._sleep(seconds)

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)
