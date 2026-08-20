"""Jira Cloud connector.

Issues are fetched with `expand=changelog`. Without the changelog none of the
flow metrics can be computed at all, because the issue resource carries only the
current status (§8). Board configuration is re-fetched every sync so a column
added in Jira cannot silently vanish from the dashboard.

The board is kanban (§5): `/board/{id}/sprint` is never requested.

Issues come from the enhanced search endpoint, `/rest/api/3/search/jql`. The old
`/rest/api/3/search` was removed and now answers 410 (CHANGE-2046). The
replacement pages by opaque cursor -- `nextPageToken` plus `isLast` -- and no
longer reports a `total`, so the page loop is bounded by the cursor rather than
by `startAt >= total`. Because the JQL orders by `updated ASC`, the newest
`updated` seen so far is a valid resume point even when the loop stops early, so
a short sync costs a re-fetch and never a silently skipped issue. Jira has been
observed handing back the same token indefinitely with `isLast` never true; a
repeated token stops the loop and reports `issues` as `partial` rather than
spinning forever inside the sync lock.

`expand=changelog` is still accepted here, so issues and their history arrive in
one request and no separate `/changelog/bulkfetch` pass is needed. Should that
ever stop populating, every issue would arrive with no `changelog` key at all --
indistinguishable from an issue that never moved -- so its absence is reported
as `partial` too.

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
from waypoint.sources.http import HttpClient, json_body

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
        self._repeated_token: str | None = None
        self._missing_changelogs: list[str] = []

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
        return str(json_body(response, "The Jira Agile API").get("type", ""))

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
            self._record_partials()
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

    def _record_partials(self) -> None:
        """Turn what the page loop noticed into entity status.

        Both conditions mean the same thing: something is missing, and the
        records already yielded would otherwise look complete.
        """
        if self._repeated_token:
            self._status["issues"].status = PARTIAL
            self._status["issues"].error = (
                "Jira handed back the same pagination token twice, so the issue "
                "page loop was stopped rather than left to run forever. Issues "
                "updated after the recorded watermark may not have been fetched; "
                "re-run `waypoint sync`."
            )
        notes = []
        if self._missing_changelogs:
            keys = ", ".join(self._missing_changelogs)
            notes.append(
                f"No changelog was returned for {keys} despite `expand=changelog`. "
                f"Cycle time, stall detection, item age, and historical WIP for "
                f"these issues cannot be computed at all."
            )
        if self._truncated_changelogs:
            keys = ", ".join(self._truncated_changelogs)
            notes.append(
                f"Changelog truncated for {keys}: Jira returned fewer history "
                f"entries than `total` reported. Cycle time, stall detection, "
                f"item age, and historical WIP for these issues may be understated."
            )
        if notes:
            self._status["changelogs"].status = PARTIAL
            self._status["changelogs"].error = " ".join(notes)

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
        token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            params: dict[str, object] = {
                "jql": self._jql(watermark),
                "expand": "changelog",
                "fields": fields,
                "maxResults": self.page_size,
            }
            if token:
                params["nextPageToken"] = token
            response = self.http.get(f"{self.base}{API_PATH}/search/jql", params=params)
            body = json_body(response, "The Jira search API")
            fetched_at = clock.iso(clock.now())
            for issue in body.get("issues", []):
                key = issue["key"]
                updated = issue.get("fields", {}).get("updated")
                self._note_watermark(updated)
                self._status["issues"].count += 1
                yield RawRecord("jira", "issues", key, fetched_at, issue)

                changelog = issue.get("changelog")
                if changelog is None:
                    self._missing_changelogs.append(key)
                    changelog = {"histories": []}
                self._note_changelog_truncation(key, changelog)
                self._status["changelogs"].count += 1
                yield RawRecord("jira", "changelogs", f"{key}:changelog", fetched_at, changelog)

            token = body.get("nextPageToken")
            if body.get("isLast") or not token:
                return
            if token in seen_tokens:
                self._repeated_token = token
                return
            seen_tokens.add(token)

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
            json_body(response, "The Jira board configuration API"),
        )
