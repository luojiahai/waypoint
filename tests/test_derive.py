from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.config import load_config
from waypoint.store.derive import days_between, derive_all, hours_between
from tests.factories import (
    insert_issue, insert_person, insert_pr, insert_repo, insert_review, insert_transition, make_db
)

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def con(tmp_path: Path):
    connection = make_db(tmp_path)
    insert_person(connection, "alex-rivera", "Alex Rivera", github_login="arivera")
    insert_person(connection, "bo-chen", "Bo Chen", github_login="bchen")
    insert_person(connection, "dependabot", "Dependabot", github_login="dependabot")
    insert_repo(connection, "platform/api")
    return connection


@pytest.fixture
def cfg(waypoint_root: Path):
    return load_config(waypoint_root)


def test_hours_and_days_between():
    assert hours_between("2026-08-19T00:00:00Z", "2026-08-19T06:00:00Z") == 6.0
    assert days_between("2026-08-17T00:00:00Z", "2026-08-19T12:00:00Z") == 2.5
    assert hours_between(None, "2026-08-19T06:00:00Z") is None
    assert hours_between("2026-08-19T06:00:00Z", None) is None


def test_time_to_first_review_runs_from_ready_for_review(con, cfg):
    insert_pr(con, "platform/api#1", ready_at="2026-08-14T10:00:00Z", author="alex-rivera")
    insert_review(con, "r1", "platform/api#1", "bo-chen", submitted_at="2026-08-15T10:00:00Z")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_to_first_review FROM pr_flow").fetchone()[0] == 24.0


def test_the_authors_own_review_does_not_count(con, cfg):
    insert_pr(con, "platform/api#1", ready_at="2026-08-14T10:00:00Z", author="alex-rivera")
    insert_review(con, "r1", "platform/api#1", "alex-rivera", submitted_at="2026-08-14T11:00:00Z")
    insert_review(con, "r2", "platform/api#1", "bo-chen", submitted_at="2026-08-16T10:00:00Z")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_to_first_review FROM pr_flow").fetchone()[0] == 48.0


def test_bot_reviews_are_excluded(con, cfg):
    insert_pr(con, "platform/api#1", ready_at="2026-08-14T10:00:00Z", author="alex-rivera")
    insert_review(con, "r1", "platform/api#1", "dependabot", submitted_at="2026-08-14T11:00:00Z")
    insert_review(con, "r2", "platform/api#1", "bo-chen", submitted_at="2026-08-16T10:00:00Z")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_to_first_review FROM pr_flow").fetchone()[0] == 48.0


def test_cycle_time_runs_from_first_commit_to_merge(con, cfg):
    insert_pr(con, "platform/api#1", first_commit_at="2026-08-13T10:00:00Z",
              merged_at="2026-08-16T10:00:00Z", state="MERGED")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_to_merge FROM pr_flow").fetchone()[0] == 72.0


def test_unmerged_pull_requests_have_no_cycle_time(con, cfg):
    insert_pr(con, "platform/api#1", first_commit_at="2026-08-13T10:00:00Z", state="OPEN")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_to_merge FROM pr_flow").fetchone()[0] is None


def test_review_wait_current_is_set_only_for_open_unreviewed_prs(con, cfg):
    insert_pr(con, "platform/api#1", ready_at="2026-08-17T12:00:00Z", state="OPEN")
    insert_pr(con, "platform/api#2", number=2, ready_at="2026-08-17T12:00:00Z", state="OPEN")
    insert_review(con, "r1", "platform/api#2", "bo-chen", submitted_at="2026-08-18T12:00:00Z")
    insert_pr(con, "platform/api#3", number=3, ready_at="2026-08-17T12:00:00Z", state="MERGED",
              merged_at="2026-08-18T12:00:00Z")
    derive_all(con, cfg, now=NOW)
    waits = dict(con.execute("SELECT pr_id, review_wait_current FROM pr_flow"))
    assert waits["platform/api#1"] == 48.0
    assert waits["platform/api#2"] is None
    assert waits["platform/api#3"] is None


def test_time_in_review_is_first_review_to_merge(con, cfg):
    insert_pr(con, "platform/api#1", ready_at="2026-08-14T10:00:00Z",
              merged_at="2026-08-17T10:00:00Z", state="MERGED")
    insert_review(con, "r1", "platform/api#1", "bo-chen", submitted_at="2026-08-15T10:00:00Z")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_in_review FROM pr_flow").fetchone()[0] == 48.0


def test_issue_cycle_time_uses_status_categories_not_names(con, cfg):
    insert_issue(con, "PROJ-1", status="Shipped", status_category="Done",
                 resolved_at="2026-08-10T09:00:00Z")
    insert_transition(con, "PROJ-1", "2026-08-04T09:00:00Z", "Building")
    insert_transition(con, "PROJ-1", "2026-08-10T09:00:00Z", "Shipped")
    derive_all(con, cfg, now=NOW)
    row = con.execute("SELECT * FROM issue_flow").fetchone()
    assert row["first_in_progress_at"] == "2026-08-04T09:00:00Z"
    assert row["first_done_at"] == "2026-08-10T09:00:00Z"
    assert row["cycle_time"] == 144.0


def test_an_issue_never_started_has_no_cycle_time(con, cfg):
    insert_issue(con, "PROJ-2", status="To Do", status_category="To Do")
    derive_all(con, cfg, now=NOW)
    row = con.execute("SELECT * FROM issue_flow WHERE issue_key='PROJ-2'").fetchone()
    assert row["first_in_progress_at"] is None
    assert row["cycle_time"] is None


def test_reopened_issue_keeps_the_first_done_transition(con, cfg):
    insert_issue(con, "PROJ-3", status="In Progress", status_category="In Progress")
    insert_transition(con, "PROJ-3", "2026-08-01T09:00:00Z", "In Progress")
    insert_transition(con, "PROJ-3", "2026-08-05T09:00:00Z", "Done")
    insert_transition(con, "PROJ-3", "2026-08-07T09:00:00Z", "In Progress")
    insert_transition(con, "PROJ-3", "2026-08-12T09:00:00Z", "Done")
    derive_all(con, cfg, now=NOW)
    row = con.execute("SELECT * FROM issue_flow WHERE issue_key='PROJ-3'").fetchone()
    assert row["first_done_at"] == "2026-08-05T09:00:00Z"
    assert row["cycle_time"] == 96.0


def test_last_transition_and_days_since_use_status_changes_only(con, cfg):
    insert_issue(con, "PROJ-4", status="In Progress", status_category="In Progress")
    insert_transition(con, "PROJ-4", "2026-08-14T12:00:00Z", "In Progress")
    insert_transition(con, "PROJ-4", "2026-08-18T12:00:00Z", "Alex", field="assignee")
    derive_all(con, cfg, now=NOW)
    row = con.execute("SELECT * FROM issue_flow WHERE issue_key='PROJ-4'").fetchone()
    assert row["last_transition_at"] == "2026-08-14T12:00:00Z"
    assert row["days_since_transition"] == 5.0


def test_derive_all_is_idempotent(con, cfg):
    insert_pr(con, "platform/api#1", ready_at="2026-08-14T10:00:00Z")
    derive_all(con, cfg, now=NOW)
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT COUNT(*) FROM pr_flow").fetchone()[0] == 1


def test_review_before_ready_yields_no_time_to_first_review(con, cfg):
    insert_pr(con, "platform/api#1", ready_at="2026-08-15T10:00:00Z", author="alex-rivera")
    insert_review(con, "r1", "platform/api#1", "bo-chen", submitted_at="2026-08-14T10:00:00Z")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_to_first_review FROM pr_flow").fetchone()[0] is None


def test_merge_before_first_commit_yields_no_time_to_merge(con, cfg):
    insert_pr(con, "platform/api#1", first_commit_at="2026-08-16T10:00:00Z",
              merged_at="2026-08-13T10:00:00Z", state="MERGED")
    derive_all(con, cfg, now=NOW)
    assert con.execute("SELECT time_to_merge FROM pr_flow").fetchone()[0] is None
