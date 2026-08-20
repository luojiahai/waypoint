import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from waypoint.sources.base import EntityStatus
from waypoint.store.manifest import ManifestStore
from waypoint.web.app import create_app
from tests.factories import (
    insert_column, insert_issue, insert_person, insert_pr, insert_repo, make_db
)


def seed(project_dir: Path, *, statuses=None):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_person(con, "alex-rivera", "Alex Rivera", github_login="arivera")
    insert_repo(con, "platform/api")
    insert_column(con, 0, "In Progress", 0, 2, ["10002"])
    insert_column(con, 1, "Review", 1, None, ["10003"])
    for index in range(3):
        key = f"PROJ-{index}"
        insert_issue(con, key, status_id="10002", status_category="In Progress",
                     summary=f"Issue {index}")
        con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                    (key, "2026-08-01T12:00:00Z", None, None, "2026-08-02T12:00:00Z", 17.0))
    insert_pr(con, "platform/api#1", state="OPEN", ready_at="2026-08-10T12:00:00Z",
              title="checkout", url="https://ghe/pr/1")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)",
                ("platform/api#1", None, None, None, 216.0))
    con.commit()
    con.close()

    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("github", statuses or {
        "pull_requests": EntityStatus("pull_requests", "ok", 1),
        "reviews": EntityStatus("reviews", "ok", 0),
        "review_requests": EntityStatus("review_requests", "ok", 0),
    }, "r1", "2026-08-19T09:12:03Z")
    manifest.record("jira", {
        "issues": EntityStatus("issues", "ok", 3),
        "changelogs": EntityStatus("changelogs", "ok", 3),
        "board_config": EntityStatus("board_config", "ok", 1),
    }, "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    return TestClient(create_app(project_dir))


def test_home_renders_a_bar_per_board_column(project_dir: Path):
    body = seed(project_dir).get("/").text
    assert "In Progress" in body
    assert "Review" in body
    assert "3 / 2" in body


def test_a_column_with_no_limit_says_no_limit(project_dir: Path):
    body = seed(project_dir).get("/").text
    assert "no limit" in body


def test_throughput_sits_beside_the_board_section_label(project_dir: Path):
    body = seed(project_dir).get("/").text
    assert "done in last 14d" in body


def test_the_oldest_item_in_flight_is_named(project_dir: Path):
    body = seed(project_dir).get("/").text
    assert "Oldest in flight:" in body


def test_the_register_carries_only_threshold_crossing_items(project_dir: Path):
    body = seed(project_dir).get("/").text
    assert "has had no review for" in body
    assert "HIGH" in body or "MED" in body


def test_the_queues_are_complete_inventories(project_dir: Path):
    body = seed(project_dir).get("/").text
    assert "open prs" in body.lower()
    assert "PROJ-0" in body and "PROJ-1" in body


def test_a_queue_longer_than_four_rows_shows_a_more_line(project_dir: Path):
    root = project_dir / ".waypoint"
    client = seed(project_dir)
    from waypoint.store.index import connect

    con = connect(root / "index.db")
    for index in range(3, 9):
        key = f"PROJ-{index}"
        insert_issue(con, key, status_id="10002", status_category="In Progress")
        con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                    (key, "2026-08-01T12:00:00Z", None, None, "2026-08-02T12:00:00Z", 17.0))
    con.commit()
    con.close()
    assert "more" in client.get("/").text


def test_an_empty_register_says_nothing_crossed_a_threshold(project_dir: Path):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_column(con, 0, "In Progress", 0, 4, ["10002"])
    con.commit()
    con.close()
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("jira", {
        "issues": EntityStatus("issues", "ok", 0),
        "changelogs": EntityStatus("changelogs", "ok", 0),
        "board_config": EntityStatus("board_config", "ok", 1),
    }, "r1", "2026-08-19T09:12:03Z")
    manifest.record("github", {
        "pull_requests": EntityStatus("pull_requests", "ok", 0),
        "reviews": EntityStatus("reviews", "ok", 0),
        "review_requests": EntityStatus("review_requests", "ok", 0),
    }, "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    body = TestClient(create_app(project_dir)).get("/").text
    assert "Nothing crossed a threshold." in body
    assert "evaluated" in body


def test_an_empty_board_says_nothing_in_progress(project_dir: Path):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_column(con, 0, "In Progress", 0, 4, ["10002"])
    con.commit()
    con.close()
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("jira", {
        "issues": EntityStatus("issues", "ok", 0),
        "changelogs": EntityStatus("changelogs", "ok", 0),
        "board_config": EntityStatus("board_config", "ok", 1),
    }, "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    assert "Nothing in progress." in TestClient(create_app(project_dir)).get("/").text


def test_a_partial_github_sync_demotes_the_pr_queue_only(project_dir: Path):
    client = seed(project_dir, statuses={
        "pull_requests": EntityStatus("pull_requests", "partial", 62, error="rate limited"),
        "reviews": EntityStatus("reviews", "ok", 0),
        "review_requests": EntityStatus("review_requests", "ok", 0),
    })
    body = client.get("/").text
    assert "PARTIAL" in body
    assert "demoted-partial" in body
    assert "rate limited" in body


def test_every_risk_row_links_to_the_underlying_item(project_dir: Path):
    body = seed(project_dir).get("/").text
    assert 'href="https://ghe/pr/1"' in body


def test_home_renders_no_action_controls_of_its_own(project_dir: Path, monkeypatch):
    # Override 2: the Analyze button only renders when Claude Code is on
    # PATH, so pin claude_available() rather than letting the assertion
    # depend on whether the machine running the tests has it installed.
    # claude_available() is called fresh per request, so one client covers
    # both cases.
    client = seed(project_dir)

    monkeypatch.setattr("waypoint.skills_runner.claude_available", lambda runner="claude": True)
    body = client.get("/").text
    buttons = body.count("<button")
    assert buttons == 2  # the Sync button in the chrome, and the Analyze button

    monkeypatch.setattr("waypoint.skills_runner.claude_available", lambda runner="claude": False)
    body = client.get("/").text
    buttons = body.count("<button")
    assert buttons == 1  # with Claude absent, only the Sync button remains


def _write_delivery_risk_report(root: Path, *, digest: str) -> None:
    """A well-formed delivery-risk sidecar, stamped with `digest` as its inputs."""
    from datetime import UTC, datetime

    from waypoint.store.reports import ReportStore

    ReportStore(root).write(
        "waypoint:delivery-risk",
        {
            "skill": "waypoint:delivery-risk",
            "generated_at": "2026-08-19T09:40:00Z",
            "window": {"from": "2026-08-05", "to": "2026-08-19"},
            "inputs_digest": digest,
            "items": [
                {
                    "severity": "high",
                    "title": "Checkout rework has no reviewer coverage",
                    "body": "Only Alex has touched it.",
                    "evidence": [{"type": "pull_request", "ref": "PR #1", "url": "https://ghe/pr/1"}],
                }
            ],
        },
        "# risks\n",
        at=datetime(2026, 8, 19, 9, 40, tzinfo=UTC),
    )


def panel_html(body: str, label: str) -> str:
    """The one panel whose section label is `label`, up to the start of its body.

    Asserting on the whole page cannot tell the register's badge from the board
    panel's badge beside it -- and on this page both read the same entities, so
    a page-wide `"FAILED" in body` passes even when the register is undemoted.
    """
    at = body.index(f'<span class="section-label">{label}</span>')
    start = max(m.start() for m in re.finditer(r'<div class="panel[ "]', body) if m.start() < at)
    return body[start : body.index('<div class="panel-body">', at)]


def test_a_fresh_report_does_not_un_demote_a_failed_register(project_dir: Path):
    """`stale_status` returns a truthy OK dataclass for a fresh report, so a
    "report status if there is one" rule would throw the FAILED badge and its
    reason away exactly when the register is least trustworthy (§4)."""
    root = project_dir / ".waypoint"
    client = seed(project_dir)
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("jira", {
        "issues": EntityStatus("issues", "failed", 0, error="401 from Jira"),
        "changelogs": EntityStatus("changelogs", "failed", 0, error="401 from Jira"),
        "board_config": EntityStatus("board_config", "ok", 1),
    }, "r2", "2026-08-19T09:30:00Z")
    store.save(manifest)

    register = panel_html(client.get("/").text, "risk register")
    assert "demoted-failed" in register
    assert "FAILED" in register

    _write_delivery_risk_report(root, digest=manifest.digest())
    body = client.get("/").text
    assert "SKILL" in body  # the fresh report really is being read
    register = panel_html(body, "risk register")
    assert "demoted-failed" in register
    assert "FAILED" in register
    assert "401 from Jira" in register


def test_a_report_whose_digest_moved_on_still_reads_as_stale(project_dir: Path):
    """Task 29's behaviour: with the manifest otherwise ok, a report generated
    against different inputs demotes the register to STALE."""
    root = project_dir / ".waypoint"
    client = seed(project_dir)
    _write_delivery_risk_report(root, digest="sha256:something-else")
    register = panel_html(client.get("/").text, "risk register")
    assert "STALE" in register
    assert "demoted-stale" in register
    assert "changed since" in register
