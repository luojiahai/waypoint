import json
from datetime import UTC, datetime
from pathlib import Path

from waypoint.config import load_config
from waypoint.sources.base import RawRecord
from waypoint.store.index import build, connect, issue_key_pattern
from waypoint.store.raw import RawStore

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def issue(key, **fields):
    base = {
        "summary": "Checkout rework",
        "issuetype": {"name": "Story"},
        "status": {"id": "10002", "name": "In Progress",
                   "statusCategory": {"key": "indeterminate", "name": "In Progress"}},
        "assignee": {"accountId": "acct-alex"},
        "reporter": {"accountId": "acct-bo"},
        "labels": ["checkout"],
        "parent": {"key": "PROJ-10"},
        "customfield_10016": 5,
        "created": "2026-08-01T09:00:00.000+0000",
        "updated": "2026-08-18T12:00:00.000+0000",
        "resolutiondate": None,
    }
    base.update(fields)
    return RawRecord("jira", "issues", key, "2026-08-19T09:00:00Z",
                     {"key": key, "self": f"https://example.atlassian.net/rest/api/3/issue/{key}",
                      "fields": base})


def changelog(key, histories):
    return RawRecord("jira", "changelogs", f"{key}:changelog", "2026-08-19T09:00:00Z",
                     {"histories": histories})


BOARD = RawRecord("jira", "board_config", "board:42", "2026-08-19T09:00:00Z", {
    "id": 42, "name": "PROJ board", "type": "kanban",
    "columnConfig": {"columns": [
        {"name": "To Do", "statuses": [{"id": "10001"}], "max": None},
        {"name": "In Progress", "statuses": [{"id": "10002"}, {"id": "10005"}], "max": 4},
        {"name": "Done", "statuses": [{"id": "10004"}], "max": None},
    ]},
})


def test_issues_are_normalized(project_dir: Path):
    root = project_dir / ".waypoint"
    RawStore(root).write([issue("PROJ-97")], "run-1")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    row = con.execute("SELECT * FROM jira_issues").fetchone()
    assert row["key"] == "PROJ-97"
    assert row["type"] == "Story"
    assert row["status_category"] == "In Progress"
    assert row["status_id"] == "10002"
    assert row["assignee_person_id"] == "alex-rivera"
    assert row["parent_key"] == "PROJ-10"
    assert row["story_points"] == 5.0
    assert json.loads(row["labels"]) == ["checkout"]
    assert row["created_at"] == "2026-08-01T09:00:00Z"
    assert row["url"] == "https://example.atlassian.net/browse/PROJ-97"


def test_unassigned_issue_lands_in_the_unattributed_bucket(project_dir: Path):
    root = project_dir / ".waypoint"
    RawStore(root).write([issue("PROJ-99", assignee=None)], "run-1")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert con.execute("SELECT assignee_person_id FROM jira_issues").fetchone()[0] == "unattributed"


def test_unknown_account_id_is_recorded_as_unattributed(project_dir: Path):
    root = project_dir / ".waypoint"
    RawStore(root).write([issue("PROJ-99", assignee={"accountId": "acct-ghost"})], "run-1")
    result = build(root, load_config(root), now=NOW)
    assert ("jira", "acct-ghost", "assignee", 1) in result.unattributed


def test_story_points_are_ignored_when_the_field_is_not_configured(project_dir: Path):
    root = project_dir / ".waypoint"
    text = (root / "config.toml").read_text().replace('"customfield_10016"', '""')
    (root / "config.toml").write_text(text)
    RawStore(root).write([issue("PROJ-97")], "run-1")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert con.execute("SELECT story_points FROM jira_issues").fetchone()[0] is None


def test_flagged_issues_are_marked(project_dir: Path):
    root = project_dir / ".waypoint"
    RawStore(root).write([issue("PROJ-97", labels=["Flagged"])], "run-1")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert con.execute("SELECT flagged FROM jira_issues").fetchone()[0] == 1


def test_status_transitions_are_extracted_from_the_changelog(project_dir: Path):
    root = project_dir / ".waypoint"
    RawStore(root).write(
        [
            issue("PROJ-97"),
            changelog("PROJ-97", [
                {"id": "1", "created": "2026-08-04T09:00:00.000+0000",
                 "author": {"accountId": "acct-alex"},
                 "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"},
                           {"field": "assignee", "fromString": None, "toString": "Alex"}]},
                {"id": "2", "created": "2026-08-09T09:00:00.000+0000",
                 "author": {"accountId": "acct-bo"},
                 "items": [{"field": "status", "fromString": "In Progress", "toString": "Review"}]},
            ]),
        ],
        "run-1",
    )
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    rows = con.execute(
        "SELECT * FROM issue_transitions ORDER BY changed_at, field"
    ).fetchall()
    assert [(r["field"], r["to_value"], r["changed_at"]) for r in rows] == [
        ("assignee", "Alex", "2026-08-04T09:00:00Z"),
        ("status", "In Progress", "2026-08-04T09:00:00Z"),
        ("status", "Review", "2026-08-09T09:00:00Z"),
    ]
    assert rows[2]["author_person_id"] == "bo-chen"


def test_board_columns_keep_order_limits_and_status_ids(project_dir: Path):
    root = project_dir / ".waypoint"
    RawStore(root).write([BOARD], "run-1")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    rows = con.execute("SELECT * FROM board_columns ORDER BY position").fetchall()
    assert [r["name"] for r in rows] == ["To Do", "In Progress", "Done"]
    assert rows[0]["wip_limit"] is None
    assert rows[1]["wip_limit"] == 4
    assert json.loads(rows[1]["status_ids"]) == ["10002", "10005"]


def test_issue_key_pattern_is_word_bounded():
    pattern = issue_key_pattern("PROJ")
    assert pattern.findall("PROJ-97 and PROJ-113") == ["PROJ-97", "PROJ-113"]
    assert pattern.findall("XPROJ-97") == []
    assert pattern.findall("proj-97") == []


def test_links_are_found_in_title_branch_and_body(project_dir: Path):
    root = project_dir / ".waypoint"
    prs = [
        RawRecord("github", "pull_requests", "platform/api#1", "2026-08-19T09:00:00Z", {
            "number": 1, "title": "PROJ-97 checkout", "url": "u", "state": "OPEN",
            "isDraft": False, "body": "", "createdAt": "2026-08-14T10:00:00Z",
            "updatedAt": "2026-08-14T10:00:00Z", "mergedAt": None, "closedAt": None,
            "additions": 1, "deletions": 1, "changedFiles": 1, "baseRefName": "main",
            "headRefName": "x", "author": {"login": "arivera"}, "labels": {"nodes": []},
            "commits": {"nodes": []}, "timelineItems": {"nodes": []}, "reviews": {"nodes": []},
        }),
        RawRecord("github", "pull_requests", "platform/api#2", "2026-08-19T09:00:00Z", {
            "number": 2, "title": "no key here", "url": "u", "state": "OPEN",
            "isDraft": False, "body": "closes PROJ-98", "createdAt": "2026-08-14T10:00:00Z",
            "updatedAt": "2026-08-14T10:00:00Z", "mergedAt": None, "closedAt": None,
            "additions": 1, "deletions": 1, "changedFiles": 1, "baseRefName": "main",
            "headRefName": "feature/PROJ-99-thing", "author": {"login": "arivera"},
            "labels": {"nodes": []}, "commits": {"nodes": []},
            "timelineItems": {"nodes": []}, "reviews": {"nodes": []},
        }),
    ]
    RawStore(root).write(prs + [issue("PROJ-97"), issue("PROJ-98"), issue("PROJ-99")], "run-1")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    rows = con.execute("SELECT * FROM issue_pr_links ORDER BY issue_key").fetchall()
    assert [(r["issue_key"], r["pr_id"]) for r in rows] == [
        ("PROJ-97", "platform/api#1"),
        ("PROJ-98", "platform/api#2"),
        ("PROJ-99", "platform/api#2"),
    ]


def test_a_key_with_no_matching_issue_creates_no_link(project_dir: Path):
    root = project_dir / ".waypoint"
    RawStore(root).write([
        RawRecord("github", "pull_requests", "platform/api#1", "2026-08-19T09:00:00Z", {
            "number": 1, "title": "PROJ-404 ghost", "url": "u", "state": "OPEN",
            "isDraft": False, "body": "", "createdAt": "2026-08-14T10:00:00Z",
            "updatedAt": "2026-08-14T10:00:00Z", "mergedAt": None, "closedAt": None,
            "additions": 1, "deletions": 1, "changedFiles": 1, "baseRefName": "main",
            "headRefName": "x", "author": {"login": "arivera"}, "labels": {"nodes": []},
            "commits": {"nodes": []}, "timelineItems": {"nodes": []}, "reviews": {"nodes": []},
        })
    ], "run-1")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert con.execute("SELECT COUNT(*) FROM issue_pr_links").fetchone()[0] == 0
