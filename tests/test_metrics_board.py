from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.metrics import board
from tests.factories import insert_column, insert_issue, insert_person, make_db

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def con(tmp_path: Path):
    connection = make_db(tmp_path)
    insert_person(connection, "alex-rivera", "Alex Rivera", jira_account_id="acct-alex")
    insert_column(connection, 0, "To Do", 0, None, ["10001"])
    insert_column(connection, 1, "In Progress", 1, 4, ["10002"])
    insert_column(connection, 2, "Review", 2, 2, ["10003"])
    insert_column(connection, 3, "Done", 3, None, ["10004"])
    return connection


def add_in_flight(con, key, status_id, started, last_moved=None, column_status="In Progress"):
    insert_issue(con, key, status=column_status, status_id=status_id,
                 status_category="In Progress", assignee="alex-rivera")
    con.execute(
        "INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
        (key, started, None, None, last_moved or started, 0.0),
    )


def test_columns_carry_order_counts_and_limits(con):
    add_in_flight(con, "PROJ-1", "10002", "2026-08-15T12:00:00Z")
    add_in_flight(con, "PROJ-2", "10003", "2026-08-10T12:00:00Z", column_status="Review")
    cols = board.columns(con)
    assert [c.name for c in cols] == ["To Do", "In Progress", "Review", "Done"]
    assert cols[1].count == 1
    assert cols[1].limit == 4
    assert cols[1].over is False


def test_a_column_over_its_limit_is_flagged(con):
    for index in range(3):
        add_in_flight(con, f"PROJ-{index}", "10003", "2026-08-15T12:00:00Z", column_status="Review")
    cols = {c.name: c for c in board.columns(con)}
    assert cols["Review"].count == 3
    assert cols["Review"].over is True


def test_a_column_with_no_limit_is_never_over_limit(con):
    for index in range(9):
        insert_issue(con, f"PROJ-{index}", status="To Do", status_id="10001",
                     status_category="To Do")
    cols = {c.name: c for c in board.columns(con)}
    assert cols["To Do"].no_limit is True
    assert cols["To Do"].over is False
    assert cols["To Do"].bar.label == "9 · no limit"


def test_item_age_runs_from_first_in_progress_not_from_the_last_move(con):
    add_in_flight(con, "PROJ-1", "10002", "2026-08-05T12:00:00Z", last_moved="2026-08-18T12:00:00Z")
    item = board.in_flight(con, now=NOW)[0]
    assert item.age_days == 14.0
    assert item.age_text == "14d"
    assert item.stalled_days == 1.0


def test_in_flight_is_oldest_first(con):
    add_in_flight(con, "PROJ-1", "10002", "2026-08-15T12:00:00Z")
    add_in_flight(con, "PROJ-2", "10002", "2026-08-05T12:00:00Z")
    assert [i.key for i in board.in_flight(con, now=NOW)] == ["PROJ-2", "PROJ-1"]


def test_an_item_whose_status_maps_to_no_column_still_appears(con):
    add_in_flight(con, "PROJ-9", "99999", "2026-08-15T12:00:00Z")
    item = board.in_flight(con, now=NOW)[0]
    assert item.column == "Unmapped"


def test_board_strip_names_the_oldest_item_in_flight(con):
    add_in_flight(con, "PROJ-1", "10002", "2026-08-15T12:00:00Z")
    add_in_flight(con, "PROJ-7", "10003", "2026-08-01T12:00:00Z", column_status="Review")
    strip = board.board_strip(con, now=NOW)
    assert strip.oldest_line == "Oldest in flight: PROJ-7 · Review · 18d"
    assert strip.empty_message is None


def test_board_strip_with_nothing_in_flight_says_so(con):
    strip = board.board_strip(con, now=NOW)
    assert strip.empty_message == "Nothing in progress."
    assert strip.oldest_line is None


def test_board_strip_reports_whether_any_column_sets_a_limit(tmp_path: Path):
    con = make_db(tmp_path)
    insert_column(con, 0, "To Do", 0, None, ["10001"])
    assert board.board_strip(con, now=NOW).any_limits is False


def test_aging_section_lanes_the_chart_by_column(con):
    add_in_flight(con, "PROJ-1", "10002", "2026-08-15T12:00:00Z")
    add_in_flight(con, "PROJ-2", "10003", "2026-07-20T12:00:00Z", column_status="Review")
    section = board.aging_section(con, now=NOW, threshold_days=10)
    assert [lane.label for lane in section.chart.lanes] == ["To Do", "In Progress", "Review", "Done"]
    assert section.chart.threshold_days == 10
    assert section.chart.oldest_label == "PROJ-2 · 30d"


def test_aging_section_lists_only_items_past_the_threshold(con):
    add_in_flight(con, "PROJ-1", "10002", "2026-08-15T12:00:00Z")
    add_in_flight(con, "PROJ-2", "10002", "2026-07-20T12:00:00Z")
    section = board.aging_section(con, now=NOW, threshold_days=10)
    assert [item.key for item in section.past_threshold] == ["PROJ-2"]


def test_aging_section_empty_state(con):
    section = board.aging_section(con, now=NOW, threshold_days=10)
    assert section.empty_message == "Nothing in progress."
    assert section.chart.has_data is False
