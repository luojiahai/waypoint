"""Direct-insert helpers so metric tests state exactly the rows they need.

Building through raw JSONL for every metric test would make the tests about the
connectors. These write the index directly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from waypoint.store.index import SCHEMA, connect

# A fixed "recent" instant shared with tests/test_index_build_github.py's NOW
# and make_db's meta.built_at. insert_pr's created_at/updated_at defaults are
# derived from it so a PR inserted with no timestamps still lands inside any
# later windowed query, unless a test deliberately overrides them.
_DEFAULT_NOW = "2026-08-19T12:00:00Z"
_DEFAULT_CREATED_AT = "2026-08-18T09:00:00Z"
_DEFAULT_UPDATED_AT = "2026-08-19T09:00:00Z"


def make_db(tmp_path: Path) -> sqlite3.Connection:
    con = connect(tmp_path / "index.db")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO meta VALUES ('built_at', '2026-08-19T12:00:00Z')")
    insert_person(con, "unattributed", "Unattributed", active=0)
    return con


def insert_person(con, person_id, name, github_login=None, jira_account_id=None, active=1):
    con.execute(
        "INSERT INTO people VALUES (?,?,?,?,?)",
        (person_id, name, github_login, jira_account_id, active),
    )


def insert_repo(con, repo_id, url="https://ghe.example/repo"):
    con.execute("INSERT INTO repos VALUES (?,?,?)", (repo_id, repo_id, url))


def insert_pr(
    con, pr_id, *, repo_id="platform/api", number=1, author="alex-rivera", title="A PR",
    body="", state="OPEN", draft=0, created_at=_DEFAULT_CREATED_AT, ready_at=None, merged_at=None,
    closed_at=None, first_commit_at=None, updated_at=_DEFAULT_UPDATED_AT, additions=1, deletions=1,
    changed_files=1, head_ref="branch", base_ref="main", labels=(), url="https://ghe.example/pr",
):
    con.execute(
        "INSERT INTO pull_requests VALUES (" + ",".join("?" * 22) + ")",
        (
            pr_id, repo_id, number, author, author, title, body, state, draft,
            created_at, ready_at, merged_at, closed_at, first_commit_at, updated_at,
            additions, deletions, changed_files, head_ref, base_ref, json.dumps(list(labels)), url,
        ),
    )


def insert_review(con, review_id, pr_id, reviewer, state="APPROVED", submitted_at=None):
    con.execute(
        "INSERT INTO pr_reviews VALUES (?,?,?,?,?,?)",
        (review_id, pr_id, reviewer, reviewer, state, submitted_at),
    )


def insert_review_request(con, pr_id, person_id, requested_at):
    con.execute(
        "INSERT INTO pr_review_requests VALUES (?,?,?,?)",
        (pr_id, person_id, person_id, requested_at),
    )


def insert_issue(
    con, key, *, summary="An issue", type="Story", status="In Progress", status_id="10002",
    status_category="In Progress", assignee="alex-rivera", reporter="bo-chen", parent_key=None,
    labels=(), story_points=None, flagged=0, created_at="2026-08-01T09:00:00Z",
    updated_at="2026-08-18T09:00:00Z", resolved_at=None, url="https://jira.example/PROJ-1",
):
    con.execute(
        "INSERT INTO jira_issues VALUES (" + ",".join("?" * 16) + ")",
        (
            key, summary, type, status, status_id, status_category, assignee, reporter,
            parent_key, json.dumps(list(labels)), story_points, flagged,
            created_at, updated_at, resolved_at, url,
        ),
    )


def insert_transition(con, key, changed_at, to_value, from_value=None, field="status",
                      author="alex-rivera"):
    con.execute(
        "INSERT INTO issue_transitions VALUES (?,?,?,?,?,?)",
        (key, field, from_value, to_value, changed_at, author),
    )


def insert_column(con, column_id, name, position, wip_limit=None, status_ids=()):
    con.execute(
        "INSERT INTO board_columns VALUES (?,?,?,?,?)",
        (column_id, name, position, wip_limit, json.dumps(list(status_ids))),
    )


def insert_link(con, issue_key, pr_id):
    con.execute("INSERT OR REPLACE INTO issue_pr_links VALUES (?,?)", (issue_key, pr_id))
