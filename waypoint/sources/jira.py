"""Jira Cloud connector.

Issues are fetched with `expand=changelog`. Without the changelog none of the
flow metrics can be computed at all, because the issue resource carries only the
current status (§8). Board configuration is re-fetched every sync so a column
added in Jira cannot silently vanish from the dashboard.

The board is kanban (§5): `/board/{id}/sprint` is never requested.

Jira's `expand=changelog` embeds only a page of history alongside `startAt` /
`maxResults` / `total`. When `total` exceeds the number of `histories` returned,
the changelog was truncated. Because the changelog is the sole source of cycle
time, stall detection, item age, and historical WIP (§8), a truncated changelog
must never look identical to a complete one: the affected issues are still
yielded in full (activity is never dropped, §9), but the `changelogs` entity is
reported `partial` with the affected issue keys named in the error, so the UI
degrades the panels that read it instead of trusting silently-incomplete data.
Waypoint does not paginate `/issue/{key}/changelog` to backfill — that is out of
scope for this connector.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping

from waypoint import clock
from waypoint.config import JiraConfig
from waypoint.errors import SourceError
from waypoint.sources.base import FAILED, OK, PARTIAL, EntityStatus, RawRecord
from waypoint.sources.http import HttpClient

API_PATH = "/rest/api/3"
AGILE_PATH = "/rest/agile/1.0"

ISSUE_FIELDS = (
    "summary,issuetype,status,assignee,reporter,labels,parent,"
    "created,updated,resolutiondate"
)


class JiraSource:
    name = "jira"
    entities = ("issues", "changelogs", "board_config")

    def __init__(
        self,
        cfg: JiraConfig,
        email: str,
        token: str,
        *,
        http: HttpClient,
        page_size: int = 100,
    ) -> None:
        self.cfg = cfg
        self.http = http
        self.page_size = page_size
        credential = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.http.client.headers.update(
            {"Authorization": f"Basic {credential}", "Accept": "application/json"}
        )
        self._status = {
            entity: EntityStatus(entity=entity, status=OK) for entity in self.entities
        }
        self._watermark: str | None = None
        self._truncated_changelogs: list[str] = []

    @property
    def base(self) -> str:
        return f"https://{self.cfg.site}"

    def status(self) -> dict[str, EntityStatus]:
        return self._status

    def reachable(self) -> bool:
        """Basic connectivity and auth check `waypoint doctor` runs up front."""
        self.http.get(f"{self.base}{API_PATH}/myself")
        return True

    def board_type(self) -> str:
        """Used by `waypoint doctor` to reject a non-kanban board up front."""
        response = self.http.get(f"{self.base}{AGILE_PATH}/board/{self.cfg.board_id}")
        return str(response.json().get("type", ""))

    def board_configuration_readable(self) -> bool:
        """Used by `waypoint doctor` to confirm the board configuration endpoint is readable."""
        self.http.get(f"{self.base}{AGILE_PATH}/board/{self.cfg.board_id}/configuration")
        return True

    def fetch(self, since: Mapping[str, str | None]) -> Iterator[RawRecord]:
        watermark = since.get("issues")
        try:
            yield from self._fetch_issues(watermark)
            for entity in ("issues", "changelogs"):
                self._status[entity].watermark = self._watermark or watermark
            if self._truncated_changelogs:
                keys = ", ".join(self._truncated_changelogs)
                self._status["changelogs"].status = PARTIAL
                self._status["changelogs"].error = (
                    f"Changelog truncated for {keys}: Jira returned fewer history "
                    f"entries than `total` reported. Cycle time, stall detection, "
                    f"item age, and historical WIP for these issues may be understated."
                )
        except SourceError as exc:
            for entity in ("issues", "changelogs"):
                self._status[entity].status = FAILED
                self._status[entity].error = exc.message
                self._status[entity].watermark = watermark

        try:
            yield from self._fetch_board_config()
        except SourceError as exc:
            self._status["board_config"].status = FAILED
            self._status["board_config"].error = exc.message

    def _jql(self, watermark: str | None) -> str:
        clauses = [f'project = "{self.cfg.project_key}"']
        if watermark:
            stamp = clock.parse(watermark).strftime("%Y-%m-%d %H:%M")
            clauses.append(f'updated >= "{stamp}"')
        return " AND ".join(clauses) + " ORDER BY updated ASC"

    def _fetch_issues(self, watermark: str | None) -> Iterator[RawRecord]:
        fields = ISSUE_FIELDS
        if self.cfg.story_points_field:
            fields = f"{fields},{self.cfg.story_points_field}"
        start_at = 0
        while True:
            response = self.http.get(
                f"{self.base}{API_PATH}/search",
                params={
                    "jql": self._jql(watermark),
                    "expand": "changelog",
                    "fields": fields,
                    "startAt": start_at,
                    "maxResults": self.page_size,
                },
            )
            body = response.json()
            issues = body.get("issues", [])
            if not issues:
                return
            fetched_at = clock.iso(clock.now())
            for issue in issues:
                key = issue["key"]
                updated = issue.get("fields", {}).get("updated")
                self._note_watermark(updated)
                self._status["issues"].count += 1
                yield RawRecord("jira", "issues", key, fetched_at, issue)

                changelog = issue.get("changelog") or {"histories": []}
                self._note_changelog_truncation(key, changelog)
                self._status["changelogs"].count += 1
                yield RawRecord("jira", "changelogs", f"{key}:changelog", fetched_at, changelog)

            start_at += len(issues)
            if start_at >= int(body.get("total", start_at)):
                return

    def _note_watermark(self, updated: str | None) -> None:
        if not updated:
            return
        stamp = clock.iso(clock.parse(updated))
        if self._watermark is None or stamp > self._watermark:
            self._watermark = stamp

    def _note_changelog_truncation(self, key: str, changelog: dict) -> None:
        total = changelog.get("total")
        if total is None:
            return
        histories = changelog.get("histories", [])
        if total > len(histories):
            self._truncated_changelogs.append(key)

    def _fetch_board_config(self) -> Iterator[RawRecord]:
        response = self.http.get(
            f"{self.base}{AGILE_PATH}/board/{self.cfg.board_id}/configuration"
        )
        self._status["board_config"].count = 1
        yield RawRecord(
            "jira",
            "board_config",
            f"board:{self.cfg.board_id}",
            clock.iso(clock.now()),
            response.json(),
        )
