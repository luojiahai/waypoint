"""The only module that knows the normalized schema.

Build reads immutable raw snapshots and writes a disposable index. It is
idempotent, resolves duplicates by last-writer-wins on `fetched_at`, and swaps a
temporary database in atomically so a failed build leaves the working index
untouched (§15).

Unit convention: every duration column here is HOURS, except
`issue_flow.days_since_transition`, which is days as of build time. Metric
functions compute live ages from timestamps and their own `now` argument.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from waypoint import clock
from waypoint.config import Config
from waypoint.roster import UNATTRIBUTED, Roster
from waypoint.sources.base import RawRecord
from waypoint.store.raw import RawStore

SCHEMA = """
CREATE TABLE people (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, github_login TEXT,
    jira_account_id TEXT, active INTEGER NOT NULL
);
CREATE TABLE repos (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT);
CREATE TABLE pull_requests (
    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, number INTEGER NOT NULL,
    author_person_id TEXT NOT NULL, author_login TEXT, title TEXT, body TEXT,
    state TEXT, draft INTEGER, created_at TEXT, ready_at TEXT, merged_at TEXT,
    closed_at TEXT, first_commit_at TEXT, updated_at TEXT,
    additions INTEGER, deletions INTEGER, changed_files INTEGER,
    head_ref TEXT, base_ref TEXT, labels TEXT, url TEXT
);
CREATE TABLE pr_reviews (
    id TEXT PRIMARY KEY, pr_id TEXT NOT NULL, reviewer_person_id TEXT NOT NULL,
    reviewer_login TEXT, state TEXT, submitted_at TEXT
);
CREATE TABLE pr_review_requests (
    pr_id TEXT NOT NULL, requested_person_id TEXT NOT NULL, requested_login TEXT,
    requested_at TEXT, PRIMARY KEY (pr_id, requested_login)
);
CREATE TABLE jira_issues (
    key TEXT PRIMARY KEY, summary TEXT, type TEXT, status TEXT, status_id TEXT,
    status_category TEXT, assignee_person_id TEXT NOT NULL, reporter_person_id TEXT,
    parent_key TEXT, labels TEXT, story_points REAL, flagged INTEGER,
    created_at TEXT, updated_at TEXT, resolved_at TEXT, url TEXT
);
CREATE TABLE issue_transitions (
    issue_key TEXT NOT NULL, field TEXT, from_value TEXT, to_value TEXT,
    changed_at TEXT, author_person_id TEXT
);
CREATE TABLE board_columns (
    id INTEGER PRIMARY KEY, name TEXT, position INTEGER,
    wip_limit INTEGER, status_ids TEXT
);
CREATE TABLE issue_pr_links (issue_key TEXT, pr_id TEXT, PRIMARY KEY (issue_key, pr_id));
CREATE TABLE sync_runs (
    id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT,
    counts TEXT, manifest_digest TEXT
);
CREATE TABLE pr_flow (
    pr_id TEXT PRIMARY KEY, time_to_first_review REAL, time_to_merge REAL,
    time_in_review REAL, review_wait_current REAL
);
CREATE TABLE issue_flow (
    issue_key TEXT PRIMARY KEY, first_in_progress_at TEXT, first_done_at TEXT,
    cycle_time REAL, last_transition_at TEXT, days_since_transition REAL
);
CREATE TABLE unattributed (
    source TEXT, identity TEXT, kind TEXT, count INTEGER,
    PRIMARY KEY (source, identity, kind)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX idx_pr_repo ON pull_requests(repo_id);
CREATE INDEX idx_pr_author ON pull_requests(author_person_id);
CREATE INDEX idx_review_pr ON pr_reviews(pr_id);
CREATE INDEX idx_transitions_key ON issue_transitions(issue_key, changed_at);
CREATE INDEX idx_issue_status ON jira_issues(status_category);
"""


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def latest_records(store: RawStore, source: str, entity: str) -> list[RawRecord]:
    """Last-writer-wins on `fetched_at` per id, ordered by id for stable output."""
    winners: dict[str, RawRecord] = {}
    for record in store.read(source, entity):
        current = winners.get(record.id)
        if current is None or record.fetched_at >= current.fetched_at:
            winners[record.id] = record
    return [winners[key] for key in sorted(winners)]


@dataclass
class BuildResult:
    tables: dict[str, int] = field(default_factory=dict)
    unattributed: list[tuple[str, str, str, int]] = field(default_factory=list)


class _Unattributed:
    """Collects identities seen in data but absent from the roster (§9).

    Configured bot logins are not unrostered people, and the Sync page's
    roster-health hint tells the user to add them to `github.bot_logins` --
    so this is the filter that makes doing so change anything. Matching is
    case-insensitive and GitHub-only, exactly as `store.derive` already
    treats the same list: a Jira account id is not a GitHub login and must
    not be silenced by one.
    """

    def __init__(self, bot_logins: Iterable[str] = ()) -> None:
        self.counts: dict[tuple[str, str, str], int] = {}
        self.bots = {login.casefold() for login in bot_logins}

    def note(self, source: str, identity: str | None, kind: str) -> None:
        if not identity:
            return
        if source == "github" and identity.casefold() in self.bots:
            return
        key = (source, identity, kind)
        self.counts[key] = self.counts.get(key, 0) + 1

    def rows(self) -> list[tuple[str, str, str, int]]:
        return [(s, i, k, c) for (s, i, k), c in sorted(self.counts.items())]


def _pr_fields(payload: dict) -> dict:
    """Read both the GraphQL node shape and the REST shape."""
    graphql = "createdAt" in payload
    if graphql:
        ready = None
        for event in (payload.get("timelineItems") or {}).get("nodes") or []:
            if event.get("__typename") == "ReadyForReviewEvent":
                created = event.get("createdAt")
                if created and (ready is None or created < ready):
                    ready = created
        if ready is None and not payload.get("isDraft"):
            ready = payload.get("createdAt")
        commits = (payload.get("commits") or {}).get("nodes") or []
        first_commit = commits[0]["commit"]["authoredDate"] if commits else None
        return {
            "number": payload["number"],
            "title": payload.get("title"),
            "body": payload.get("body") or "",
            "state": (payload.get("state") or "").upper(),
            "draft": 1 if payload.get("isDraft") else 0,
            "created_at": payload.get("createdAt"),
            "ready_at": ready,
            "merged_at": payload.get("mergedAt"),
            "closed_at": payload.get("closedAt"),
            "updated_at": payload.get("updatedAt"),
            "first_commit_at": first_commit,
            "additions": payload.get("additions"),
            "deletions": payload.get("deletions"),
            "changed_files": payload.get("changedFiles"),
            "head_ref": payload.get("headRefName"),
            "base_ref": payload.get("baseRefName"),
            "labels": [n["name"] for n in (payload.get("labels") or {}).get("nodes") or []],
            "url": payload.get("url"),
            "login": (payload.get("author") or {}).get("login"),
        }
    return {
        "number": payload["number"],
        "title": payload.get("title"),
        "body": payload.get("body") or "",
        "state": ("MERGED" if payload.get("merged_at") else (payload.get("state") or "").upper()),
        "draft": 1 if payload.get("draft") else 0,
        "created_at": payload.get("created_at"),
        "ready_at": None if payload.get("draft") else payload.get("created_at"),
        "merged_at": payload.get("merged_at"),
        "closed_at": payload.get("closed_at"),
        "updated_at": payload.get("updated_at"),
        "first_commit_at": None,
        "additions": payload.get("additions"),
        "deletions": payload.get("deletions"),
        "changed_files": payload.get("changed_files"),
        "head_ref": (payload.get("head") or {}).get("ref"),
        "base_ref": (payload.get("base") or {}).get("ref"),
        "labels": [label["name"] for label in payload.get("labels") or []],
        "url": payload.get("html_url"),
        "login": (payload.get("user") or {}).get("login"),
    }


def _load_people(con: sqlite3.Connection, roster: Roster) -> int:
    rows = [
        (p.id, p.name, p.github_login, p.jira_account_id, 1 if p.active else 0)
        for p in roster.people
    ]
    rows.append((UNATTRIBUTED, "Unattributed", None, None, 0))
    con.executemany("INSERT INTO people VALUES (?,?,?,?,?)", rows)
    return len(rows)


def _load_repos(con: sqlite3.Connection, cfg: Config) -> int:
    rows = [(repo, repo, f"{cfg.github.base_url}/{repo}") for repo in cfg.github.repos]
    con.executemany("INSERT INTO repos VALUES (?,?,?)", rows)
    return len(rows)


def _load_github(
    con: sqlite3.Connection, store: RawStore, roster: Roster, seen: _Unattributed
) -> dict[str, int]:
    counts: dict[str, int] = {}

    pr_rows = []
    for record in latest_records(store, "github", "pull_requests"):
        fields = _pr_fields(record.payload)
        repo_id = record.id.split("#", 1)[0]
        person_id = roster.resolve_github(fields["login"])
        if person_id == UNATTRIBUTED:
            seen.note("github", fields["login"], "author")
        pr_rows.append(
            (
                record.id, repo_id, fields["number"], person_id, fields["login"],
                fields["title"], fields["body"], fields["state"], fields["draft"],
                fields["created_at"], fields["ready_at"], fields["merged_at"],
                fields["closed_at"], fields["first_commit_at"], fields["updated_at"],
                fields["additions"], fields["deletions"], fields["changed_files"],
                fields["head_ref"], fields["base_ref"], json.dumps(fields["labels"]),
                fields["url"],
            )
        )
    con.executemany(
        "INSERT INTO pull_requests VALUES (" + ",".join("?" * 22) + ")", pr_rows
    )
    counts["pull_requests"] = len(pr_rows)

    known_prs = {row[0] for row in pr_rows}

    review_rows = []
    for record in latest_records(store, "github", "reviews"):
        payload = record.payload
        pr_id = payload.get("pull_request_id")
        if pr_id not in known_prs:
            continue  # a review whose PR fell outside the window has nothing to attach to
        login = (payload.get("author") or payload.get("user") or {}).get("login")
        person_id = roster.resolve_github(login)
        if person_id == UNATTRIBUTED:
            seen.note("github", login, "reviewer")
        review_rows.append(
            (
                record.id, pr_id, person_id, login,
                payload.get("state"),
                payload.get("submittedAt") or payload.get("submitted_at"),
            )
        )
    con.executemany("INSERT INTO pr_reviews VALUES (?,?,?,?,?,?)", review_rows)
    counts["pr_reviews"] = len(review_rows)

    # Override 4: dedup by (pr_id, requested_login) rather than the full id.
    # The REST connector path has no per-request timestamp and emits the
    # literal "unknown" for requested_at, while GraphQL emits the real event
    # timestamp. If the transport changes between syncs, a raw-level dedup on
    # the full id would let one real review request land as two rows. Collapse
    # here by (pr_id, login), preferring a real timestamp over "unknown".
    request_by_key: dict[tuple[str, str], tuple] = {}
    for record in latest_records(store, "github", "review_requests"):
        payload = record.payload
        pr_id = payload.get("pull_request_id")
        if pr_id not in known_prs:
            continue
        login = payload.get("login")
        key = (pr_id, login)
        requested_at = payload.get("requested_at")
        existing = request_by_key.get(key)
        if existing is not None and existing[2] != "unknown" and requested_at == "unknown":
            continue  # keep the existing row with the real timestamp
        request_by_key[key] = (pr_id, login, requested_at)
    request_rows = []
    for pr_id, login, requested_at in request_by_key.values():
        person_id = roster.resolve_github(login)
        if person_id == UNATTRIBUTED:
            seen.note("github", login, "requested_reviewer")
        request_rows.append((pr_id, person_id, login, requested_at))
    con.executemany(
        "INSERT OR REPLACE INTO pr_review_requests VALUES (?,?,?,?)", request_rows
    )
    counts["pr_review_requests"] = len(request_rows)
    return counts


def issue_key_pattern(project_key: str) -> re.Pattern:
    """`PROJ-123`, word-bounded and case-sensitive.

    This is the only linking mechanism (§10). Jira's development panel needs an
    app connection that may not exist, so it is never queried.
    """
    return re.compile(rf"\b{re.escape(project_key)}-\d+\b")


def _iso_or_none(value: str | None) -> str | None:
    return clock.iso(clock.parse(value)) if value else None


def _load_jira(
    con: sqlite3.Connection, store: RawStore, cfg: Config, roster: Roster, seen: _Unattributed
) -> dict[str, int]:
    counts: dict[str, int] = {}
    points_field = cfg.jira.story_points_field

    issue_rows = []
    for record in latest_records(store, "jira", "issues"):
        payload = record.payload
        fields = payload.get("fields", {})
        status = fields.get("status") or {}
        category = (status.get("statusCategory") or {}).get("name")
        assignee = (fields.get("assignee") or {}).get("accountId")
        reporter = (fields.get("reporter") or {}).get("accountId")
        assignee_id = roster.resolve_jira(assignee)
        if assignee and assignee_id == UNATTRIBUTED:
            seen.note("jira", assignee, "assignee")
        reporter_id = roster.resolve_jira(reporter)
        if reporter and reporter_id == UNATTRIBUTED:
            seen.note("jira", reporter, "reporter")
        labels = list(fields.get("labels") or [])
        points = fields.get(points_field) if points_field else None
        issue_rows.append(
            (
                payload["key"], fields.get("summary"),
                (fields.get("issuetype") or {}).get("name"),
                status.get("name"), status.get("id"), category,
                assignee_id, reporter_id,
                (fields.get("parent") or {}).get("key"),
                json.dumps(labels),
                float(points) if isinstance(points, (int, float)) else None,
                1 if any(label.casefold() == "flagged" for label in labels) else 0,
                _iso_or_none(fields.get("created")),
                _iso_or_none(fields.get("updated")),
                _iso_or_none(fields.get("resolutiondate")),
                f"https://{cfg.jira.site}/browse/{payload['key']}",
            )
        )
    con.executemany("INSERT INTO jira_issues VALUES (" + ",".join("?" * 16) + ")", issue_rows)
    counts["jira_issues"] = len(issue_rows)

    transition_rows = []
    for record in latest_records(store, "jira", "changelogs"):
        issue_key = record.id.removesuffix(":changelog")
        for history in record.payload.get("histories") or []:
            changed_at = _iso_or_none(history.get("created"))
            author = (history.get("author") or {}).get("accountId")
            author_id = roster.resolve_jira(author)
            if author and author_id == UNATTRIBUTED:
                seen.note("jira", author, "changelog_author")
            for item in history.get("items") or []:
                transition_rows.append(
                    (
                        issue_key, item.get("field"), item.get("fromString"),
                        item.get("toString"), changed_at, author_id,
                    )
                )
    con.executemany("INSERT INTO issue_transitions VALUES (?,?,?,?,?,?)", transition_rows)
    counts["issue_transitions"] = len(transition_rows)

    column_rows = []
    for record in latest_records(store, "jira", "board_config"):
        columns = (record.payload.get("columnConfig") or {}).get("columns") or []
        for position, column in enumerate(columns):
            status_ids = [str(s["id"]) for s in column.get("statuses") or []]
            column_rows.append(
                (
                    position, column.get("name"), position,
                    column.get("max"), json.dumps(status_ids),
                )
            )
    con.executemany("INSERT INTO board_columns VALUES (?,?,?,?,?)", column_rows)
    counts["board_columns"] = len(column_rows)
    return counts


def _link_issues_to_prs(con: sqlite3.Connection, cfg: Config) -> int:
    pattern = issue_key_pattern(cfg.jira.project_key)
    known = {row["key"] for row in con.execute("SELECT key FROM jira_issues")}
    links: set[tuple[str, str]] = set()
    for row in con.execute("SELECT id, title, body, head_ref FROM pull_requests"):
        haystack = " ".join(filter(None, (row["title"], row["head_ref"], row["body"])))
        for key in pattern.findall(haystack):
            if key in known:
                links.add((key, row["id"]))
    con.executemany("INSERT OR REPLACE INTO issue_pr_links VALUES (?,?)", sorted(links))
    return len(links)


def build(root: Path, cfg: Config, *, now: datetime) -> BuildResult:
    """Rebuild the index from raw. Atomic: the live index is replaced or untouched."""
    root = Path(root)
    target = root / "index.db"
    temporary = root / "index.db.tmp"
    if temporary.exists():
        temporary.unlink()

    store = RawStore(root)
    roster = Roster.from_config(cfg)
    seen = _Unattributed(cfg.github.bot_logins)
    result = BuildResult()

    con = connect(temporary)
    try:
        con.executescript(SCHEMA)
        result.tables["people"] = _load_people(con, roster)
        result.tables["repos"] = _load_repos(con, cfg)
        result.tables.update(_load_github(con, store, roster, seen))
        result.tables.update(_load_jira(con, store, cfg, roster, seen))
        result.tables["issue_pr_links"] = _link_issues_to_prs(con, cfg)

        from waypoint.store.derive import derive_all

        result.tables.update(derive_all(con, cfg, now=now))
        rows = seen.rows()
        con.executemany("INSERT INTO unattributed VALUES (?,?,?,?)", rows)
        result.unattributed = rows
        con.execute("INSERT INTO meta VALUES ('built_at', ?)", (clock.iso(now),))
        con.commit()
    except BaseException:
        con.close()
        temporary.unlink(missing_ok=True)
        raise
    con.close()
    temporary.replace(target)
    return result
