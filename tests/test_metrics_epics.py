from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.config import load_config
from waypoint.metrics import epics
from tests.factories import insert_issue, insert_person, make_db

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def con(tmp_path: Path):
    connection = make_db(tmp_path)
    insert_person(connection, "alex-rivera", "Alex Rivera")
    insert_issue(connection, "PROJ-10", summary="Checkout", type="Epic", status="In Progress",
                 status_category="In Progress", parent_key=None)
    return connection


@pytest.fixture
def jira(waypoint_root: Path):
    return load_config(waypoint_root).jira


def child(con, key, done_at=None, points=None):
    insert_issue(
        con, key, parent_key="PROJ-10", story_points=points,
        status="Done" if done_at else "In Progress",
        status_category="Done" if done_at else "In Progress",
        resolved_at=done_at,
    )
    con.execute(
        "INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
        (key, "2026-07-01T09:00:00Z", done_at, None, done_at or "2026-07-01T09:00:00Z", 0.0),
    )


def test_completion_is_by_child_count_when_points_are_sparse(con, jira):
    child(con, "PROJ-11", done_at="2026-08-10T09:00:00Z")
    child(con, "PROJ-12")
    child(con, "PROJ-13")
    section = epics.epics(con, now=NOW, jira=jira)
    assert section.basis == "count"
    assert section.basis_label == "by issue count"
    row = section.rows[0]
    assert (row.done, row.total) == (1, 3)
    assert row.completion_text == "1 / 3"


def test_points_are_used_when_configured_and_populated_above_eighty_percent(con, jira):
    child(con, "PROJ-11", done_at="2026-08-10T09:00:00Z", points=3)
    child(con, "PROJ-12", points=5)
    child(con, "PROJ-13", points=2)
    child(con, "PROJ-14", points=2)
    child(con, "PROJ-15", points=1)
    section = epics.epics(con, now=NOW, jira=jira)
    assert section.basis == "points"
    assert section.basis_label == "by story points"
    assert (section.rows[0].done, section.rows[0].total) == (3, 13)


def test_points_are_not_used_when_the_field_is_unconfigured(con, waypoint_root: Path):
    from waypoint.config import JiraConfig

    for index, key in enumerate(["PROJ-11", "PROJ-12", "PROJ-13"]):
        child(con, key, points=3)
    unconfigured = JiraConfig(site="s", project_key="PROJ", board_id=1, story_points_field="")
    assert epics.epics(con, now=NOW, jira=unconfigured).basis == "count"


def test_projection_uses_the_trailing_four_week_completion_rate(con, jira):
    for index in range(4):
        child(con, f"PROJ-2{index}", done_at="2026-08-05T09:00:00Z")
    for index in range(4):
        child(con, f"PROJ-3{index}")
    row = epics.epics(con, now=NOW, jira=jira).rows[0]
    assert row.projection_state in {"on_track", "drift"}
    assert row.projection_text.startswith("~")


def test_zero_trailing_rate_reads_as_no_recent_progress(con, jira):
    child(con, "PROJ-11", done_at="2026-01-05T09:00:00Z")
    child(con, "PROJ-12")
    row = epics.epics(con, now=NOW, jira=jira).rows[0]
    assert row.projection_text == "no recent progress"
    assert row.projection_state == "none"


def test_a_complete_epic_needs_no_projection(con, jira):
    child(con, "PROJ-11", done_at="2026-08-10T09:00:00Z")
    row = epics.epics(con, now=NOW, jira=jira).rows[0]
    assert row.projection_text == "complete"
    assert row.projection_state == "none"


def test_drift_against_a_due_date_is_reported_in_days(con, jira):
    con.execute("UPDATE jira_issues SET labels = ? WHERE key = 'PROJ-10'", ('["due:2026-08-25"]',))
    for index in range(4):
        child(con, f"PROJ-2{index}", done_at="2026-08-12T09:00:00Z")
    for index in range(8):
        child(con, f"PROJ-3{index}")
    row = epics.epics(con, now=NOW, jira=jira).rows[0]
    assert row.drift_days is not None
    assert "past due" in row.projection_text


def test_an_epic_with_no_children_is_not_listed(con, jira):
    assert epics.epics(con, now=NOW, jira=jira).rows == []


def test_no_epics_at_all_gives_an_empty_message(tmp_path: Path, jira):
    con = make_db(tmp_path)
    section = epics.epics(con, now=NOW, jira=jira)
    assert section.empty_message == "No epics with child issues."
