"""GitHub Enterprise connector.

One GraphQL query per repository page retrieves pull requests together with
reviews, review requests, timeline events, and the first commit date. Over REST
the same information is an N+1 request per pull request (§8), so GraphQL is the
primary path and REST is the fallback for older GHE versions.

A per-repository failure records `partial` and moves on; only a total failure
records `failed`. Nothing here interprets a payload.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from waypoint import clock
from waypoint.config import GithubConfig
from waypoint.errors import SourceError
from waypoint.sources.base import FAILED, OK, PARTIAL, EntityStatus, RawRecord
from waypoint.sources.http import HttpClient

GRAPHQL_PATH = "/api/graphql"
REST_PATH = "/api/v3"

PULL_REQUEST_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $size: Int!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    url
    pullRequests(first: $size, after: $cursor,
                 orderBy: {field: UPDATED_AT, direction: DESC},
                 states: [OPEN, MERGED, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url state isDraft body
        createdAt updatedAt mergedAt closedAt
        additions deletions changedFiles
        baseRefName headRefName
        author { login }
        labels(first: 20) { nodes { name } }
        commits(first: 1) { nodes { commit { authoredDate } } }
        timelineItems(first: 50, itemTypes: [READY_FOR_REVIEW_EVENT, REVIEW_REQUESTED_EVENT]) {
          nodes {
            __typename
            ... on ReadyForReviewEvent { createdAt }
            ... on ReviewRequestedEvent {
              createdAt
              requestedReviewer { ... on User { login } }
            }
          }
        }
        reviews(first: 50) {
          nodes { id state submittedAt author { login } }
        }
      }
    }
  }
}
"""


class GithubSource:
    name = "github"
    entities = ("pull_requests", "reviews", "review_requests")

    def __init__(
        self,
        cfg: GithubConfig,
        token: str,
        *,
        http: HttpClient,
        use_graphql: bool = True,
        page_size: int = 50,
    ) -> None:
        self.cfg = cfg
        self.http = http
        self.use_graphql = use_graphql
        self.page_size = page_size
        self.http.client.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        )
        self._status = {
            entity: EntityStatus(entity=entity, status=OK) for entity in self.entities
        }
        self._failures: list[str] = []
        self._succeeded: list[str] = []
        self._watermark: str | None = None

    def status(self) -> dict[str, EntityStatus]:
        return self._status

    # ---- fetch ----------------------------------------------------------

    def fetch(self, since: Mapping[str, str | None]) -> Iterator[RawRecord]:
        watermark = since.get("pull_requests")
        for repo in self.cfg.repos:
            try:
                yield from self._fetch_repo(repo, watermark)
                self._succeeded.append(repo)
            except SourceError as exc:
                self._failures.append(f"{repo}: {exc.message}")
        self._finalize(watermark)

    def _finalize(self, previous_watermark: str | None) -> None:
        if self._failures and not self._succeeded:
            state = FAILED
        elif self._failures:
            state = PARTIAL
        else:
            state = OK
        error = "; ".join(self._failures) or None
        new_watermark = self._watermark or previous_watermark
        for entity, status in self._status.items():
            status.status = state
            status.error = error
            status.watermark = new_watermark

    def _fetch_repo(self, repo: str, watermark: str | None) -> Iterator[RawRecord]:
        if self.use_graphql:
            yield from self._fetch_repo_graphql(repo, watermark)
        else:
            yield from self._fetch_repo_rest(repo, watermark)

    def _fetch_repo_graphql(self, repo: str, watermark: str | None) -> Iterator[RawRecord]:
        owner, _, name = repo.partition("/")
        cursor = None
        while True:
            response = self.http.post(
                self.cfg.base_url + GRAPHQL_PATH,
                json={
                    "query": PULL_REQUEST_QUERY,
                    "variables": {
                        "owner": owner,
                        "name": name,
                        "cursor": cursor,
                        "size": self.page_size,
                    },
                },
            )
            body = response.json()
            if body.get("errors"):
                message = "; ".join(e.get("message", "") for e in body["errors"])
                raise SourceError(f"GraphQL error: {message}", kind="http")
            connection = body["data"]["repository"]["pullRequests"]
            exhausted = False
            for node in connection["nodes"]:
                if watermark and node["updatedAt"] < watermark:
                    exhausted = True
                    continue
                yield from self._records_for(repo, node)
            page = connection["pageInfo"]
            if exhausted or not page["hasNextPage"]:
                return
            cursor = page["endCursor"]

    def _records_for(self, repo: str, node: dict) -> Iterator[RawRecord]:
        fetched_at = clock.iso(clock.now())
        pr_id = f"{repo}#{node['number']}"
        self._note_watermark(node.get("updatedAt"))
        self._status["pull_requests"].count += 1
        yield RawRecord("github", "pull_requests", pr_id, fetched_at, node)

        for review in node.get("reviews", {}).get("nodes", []) or []:
            payload = dict(review)
            payload["pull_request_id"] = pr_id
            self._status["reviews"].count += 1
            yield RawRecord(
                "github", "reviews", f"{pr_id}:review:{review['id']}", fetched_at, payload
            )

        for event in node.get("timelineItems", {}).get("nodes", []) or []:
            if event.get("__typename") != "ReviewRequestedEvent":
                continue
            reviewer = event.get("requestedReviewer") or {}
            login = reviewer.get("login")
            if not login:
                continue  # team review requests are not attributed to a person
            requested_at = event["createdAt"]
            payload = {
                "pull_request_id": pr_id,
                "login": login,
                "requested_at": requested_at,
            }
            self._status["review_requests"].count += 1
            yield RawRecord(
                "github",
                "review_requests",
                f"{pr_id}:requested:{login}:{requested_at}",
                fetched_at,
                payload,
            )

    def _note_watermark(self, updated_at: str | None) -> None:
        if updated_at and (self._watermark is None or updated_at > self._watermark):
            self._watermark = updated_at

    def _fetch_repo_rest(self, repo: str, watermark: str | None) -> Iterator[RawRecord]:
        raise NotImplementedError  # filled in by Task 8
