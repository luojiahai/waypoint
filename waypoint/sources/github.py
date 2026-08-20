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
from waypoint.sources.http import HttpClient, json_body

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
              requestedReviewer { ... on User { login } ... on Team { slug } }
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
            body = json_body(response, "The GitHub GraphQL API")
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
            if reviewer.get("login"):
                login = reviewer["login"]
            elif reviewer.get("slug"):
                login = f"team:{reviewer['slug']}"
            else:
                login = "unknown"
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

    def reachable(self) -> bool:
        """Basic connectivity and auth check `waypoint doctor` runs up front.

        Distinct from `probe_graphql`: this only confirms the server answers
        and the token is accepted, before anything asks whether GraphQL works.
        """
        self.http.get(f"{self.cfg.base_url}{REST_PATH}/user")
        return True

    def repo_readable(self, repo: str) -> bool:
        """Used by `waypoint doctor` to confirm the token can read each configured repo."""
        self.http.get(f"{self.cfg.base_url}{REST_PATH}/repos/{repo}")
        return True

    def probe_graphql(self) -> bool:
        """Does this GHE version answer the fields the connector needs?

        `waypoint doctor` calls this so an unsupported instance produces a clear
        message before a sync rather than a mid-backfill failure (§8).
        """
        owner, _, name = (self.cfg.repos[0] if self.cfg.repos else "x/y").partition("/")
        try:
            response = self.http.post(
                self.cfg.base_url + GRAPHQL_PATH,
                json={
                    "query": PULL_REQUEST_QUERY,
                    "variables": {"owner": owner, "name": name, "cursor": None, "size": 1},
                },
            )
        except SourceError as exc:
            if exc.kind == "auth":
                raise
            return False
        return not json_body(response, "The GitHub GraphQL API").get("errors")

    def _fetch_repo_rest(self, repo: str, watermark: str | None) -> Iterator[RawRecord]:
        """REST fallback. Reviews are an extra request per PR — the N+1 GraphQL avoids."""
        page = 1
        while True:
            response = self.http.get(
                f"{self.cfg.base_url}{REST_PATH}/repos/{repo}/pulls",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": self.page_size,
                    "page": page,
                },
            )
            nodes = json_body(response, f"The GitHub REST API for {repo}")
            if not nodes:
                return
            for node in nodes:
                if watermark and node["updated_at"] < watermark:
                    return
                yield from self._rest_records_for(repo, node)
            if len(nodes) < self.page_size:
                return
            page += 1

    def _rest_records_for(self, repo: str, node: dict) -> Iterator[RawRecord]:
        fetched_at = clock.iso(clock.now())
        pr_id = f"{repo}#{node['number']}"
        self._note_watermark(node.get("updated_at"))
        self._status["pull_requests"].count += 1
        yield RawRecord("github", "pull_requests", pr_id, fetched_at, node)

        reviews = json_body(
            self.http.get(
                f"{self.cfg.base_url}{REST_PATH}/repos/{repo}/pulls/{node['number']}/reviews"
            ),
            f"The GitHub REST API for {repo}",
        )
        for review in reviews:
            payload = dict(review)
            payload["pull_request_id"] = pr_id
            self._status["reviews"].count += 1
            yield RawRecord(
                "github", "reviews", f"{pr_id}:review:{review['id']}", fetched_at, payload
            )

        # REST exposes no per-request timestamp at all — only who is currently
        # outstanding, not when the request was made. Substituting the PR's
        # creation time would invent a plausible-looking but wrong value (a
        # PR opened in May with a review requested in August would silently
        # report a three-month review wait). Say so explicitly instead.
        requested_at = "unknown"
        for reviewer in node.get("requested_reviewers", []) or []:
            login = reviewer.get("login") or "unknown"
            yield from self._rest_review_request(pr_id, login, requested_at, fetched_at)
        # REST keeps team review requests in a separate array from user ones;
        # a team request is never dropped (§9) — its login component mirrors
        # the GraphQL path's `team:{slug}` (falling back to `unknown`) so the
        # same underlying request produces the same id under either transport.
        for team in node.get("requested_teams", []) or []:
            slug = team.get("slug")
            login = f"team:{slug}" if slug else "unknown"
            yield from self._rest_review_request(pr_id, login, requested_at, fetched_at)

    def _rest_review_request(
        self, pr_id: str, login: str, requested_at: str, fetched_at: str
    ) -> Iterator[RawRecord]:
        self._status["review_requests"].count += 1
        yield RawRecord(
            "github",
            "review_requests",
            f"{pr_id}:requested:{login}:{requested_at}",
            fetched_at,
            {
                "pull_request_id": pr_id,
                "login": login,
                "requested_at": requested_at,
            },
        )
