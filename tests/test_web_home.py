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


def test_home_renders_no_action_controls_of_its_own(project_dir: Path):
    body = seed(project_dir).get("/").text
    buttons = body.count("<button")
    assert buttons == 1  # the Sync button in the chrome, and nothing else
