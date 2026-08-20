from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.config import load_config
from waypoint.metrics import flow
from waypoint.store.derive import derive_all
from tests.factories import (
    insert_issue, insert_person, insert_pr, insert_repo, insert_review, insert_transition, make_db
)

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def con(tmp_path: Path):
    connection = make_db(tmp_path)
    insert_person(connection, "alex-rivera", "Alex Rivera", github_login="arivera")
    insert_person(connection, "bo-chen", "Bo Chen", github_login="bchen")
    insert_repo(connection, "platform/api")
    return connection


@pytest.fixture
def cfg(waypoint_root: Path):
    return load_config(waypoint_root)


def test_distribution_of_an_even_sample():
    dist = flow.distribution([1, 2, 3, 4])
    assert dist.median == 2.5
    assert dist.p75 == 3.25
    assert dist.count == 4


def test_distribution_ignores_missing_values():
    assert flow.distribution([1, None, 3]).count == 2


def test_distribution_of_nothing_is_empty_not_zero():
    dist = flow.distribution([])
    assert dist.median is None
    assert dist.p75 is None
    assert dist.count == 0


def make_reviewed_pr(con, number, ready, submitted, hours):
    insert_pr(con, f"platform/api#{number}", number=number, ready_at=ready, state="MERGED",
              merged_at=submitted)
    insert_review(con, f"r{number}", f"platform/api#{number}", "bo-chen", submitted_at=submitted)
    con.execute(
        "INSERT INTO pr_flow VALUES (?,?,?,?,?)",
        (f"platform/api#{number}", hours, hours, 0.0, None),
    )


def test_review_latency_reports_median_and_p75_in_hours(con, cfg):
    for index, hours in enumerate([4.0, 8.0, 12.0, 40.0], start=1):
        make_reviewed_pr(con, index, "2026-08-14T10:00:00Z", "2026-08-15T10:00:00Z", hours)
    panel = flow.review_latency(con, now=NOW)
    assert panel.unit == "h"
    assert panel.median_text == "10h"
    assert panel.p75_text == "19h"
    assert panel.count == 4


def test_review_latency_has_no_target_field():
    assert not any(
        "target" in name or "goal" in name for name in flow.FlowPanel.__dataclass_fields__
    )


def test_review_latency_with_no_reviewed_prs_reads_as_no_data(con, cfg):
    panel = flow.review_latency(con, now=NOW)
    assert panel.median_text == "—"
    assert panel.count == 0
    assert panel.spark.has_data is False


def test_issue_cycle_time_is_reported_in_days(con, cfg):
    insert_issue(con, "PROJ-1", status_category="Done", resolved_at="2026-08-12T09:00:00Z")
    con.execute(
        "INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
        ("PROJ-1", "2026-08-08T09:00:00Z", "2026-08-12T09:00:00Z", 96.0, "2026-08-12T09:00:00Z", 7.0),
    )
    panel = flow.issue_cycle_time(con, now=NOW)
    assert panel.unit == "d"
    assert panel.median_text == "4.0d"


def test_wip_series_is_reconstructed_from_transitions(con, cfg):
    # Override 4: the brief's original fixture gave both issues status="In
    # Progress" (only status_category differed), which collides in the
    # status -> category map derived from jira_issues and makes the
    # assertions below unsatisfiable. Distinct status names, each consistent
    # with its own status_category, let `_category_map` resolve each issue
    # into the category its transitions and the assertions require.
    insert_issue(con, "PROJ-1", status="In Progress", status_category="In Progress")
    insert_issue(con, "PROJ-2", status="Done", status_category="Done",
                 resolved_at="2026-08-15T09:00:00Z")
    insert_transition(con, "PROJ-1", "2026-08-05T09:00:00Z", "In Progress")
    insert_transition(con, "PROJ-2", "2026-08-06T09:00:00Z", "In Progress")
    insert_transition(con, "PROJ-2", "2026-08-15T09:00:00Z", "Done")
    panel = flow.wip_series(con, now=NOW, weeks=4)
    assert panel.current == 1
    assert max(panel.series) == 2
    assert panel.spark.has_data is True


def test_wip_series_resolves_a_retired_status_name_via_category_hints(con, cfg):
    # "Closed" is not any issue's *current* status here (PROJ-3 now reads
    # "Done" -- a workflow rename), so it is absent from `_category_map`'s
    # current-status snapshot. `_wip_at` must still resolve it as Done via
    # `derive._category_for`'s `_CATEGORY_HINTS` fallback ("closed" -> Done),
    # not silently default it to IN_PROGRESS and inflate the WIP count.
    insert_issue(con, "PROJ-1", status="In Progress", status_category="In Progress")
    insert_issue(con, "PROJ-3", status="Done", status_category="Done",
                 resolved_at="2026-08-10T09:00:00Z")
    insert_transition(con, "PROJ-1", "2026-08-05T09:00:00Z", "In Progress")
    insert_transition(con, "PROJ-3", "2026-08-06T09:00:00Z", "In Progress")
    insert_transition(con, "PROJ-3", "2026-08-10T09:00:00Z", "Closed")
    panel = flow.wip_series(con, now=NOW, weeks=4)
    assert panel.current == 1


def test_throughput_compares_the_trailing_window_with_the_preceding_one(con, cfg):
    for index in range(3):
        key = f"PROJ-{index}"
        insert_issue(con, key, status_category="Done", resolved_at="2026-08-15T09:00:00Z")
        insert_transition(con, key, "2026-08-15T09:00:00Z", "Done")
    for index in range(3, 5):
        key = f"PROJ-{index}"
        insert_issue(con, key, status_category="Done", resolved_at="2026-07-25T09:00:00Z")
        insert_transition(con, key, "2026-07-25T09:00:00Z", "Done")
    # Override 3: `throughput()` reads `issue_flow.first_done_at`, which only
    # exists once `derive_all` has run over the seeded transitions -- deriving
    # is an explicit build step, not a side effect of insertion.
    derive_all(con, cfg, now=NOW)
    panel = flow.throughput(con, now=NOW, window_days=14)
    assert panel.current == 3
    assert panel.previous == 2
    assert panel.summary == "3 done in last 14d · 2 in the 14d before"


def test_throughput_is_never_attributed_per_person():
    import inspect

    source = inspect.getsource(flow.throughput)
    assert "person" not in source.lower()


def test_throughput_with_nothing_done_states_zero_against_zero(con, cfg):
    panel = flow.throughput(con, now=NOW, window_days=14)
    assert panel.summary == "0 done in last 14d · 0 in the 14d before"
    assert panel.spark.has_data is True


def test_open_prs_a_zero_wait_still_reads_as_waiting(con):
    # Regression: `0.0` and `None` are both falsy, so a naive `if wait` would
    # render a PR that just became ready (`review_wait_current == 0.0`) as
    # "reviewed" -- a confidently wrong claim nobody reviewed it (§4).
    insert_pr(con, "platform/api#1", state="OPEN", url="https://ghe/pr/1")
    con.execute(
        "INSERT INTO pr_flow VALUES (?,?,?,?,?)",
        ("platform/api#1", None, None, None, 0.0),
    )
    items = flow.open_prs(con)
    assert items[0].review_wait_text == "0d waiting"


def test_open_prs_an_unknown_wait_reads_as_reviewed(con):
    insert_pr(con, "platform/api#2", state="OPEN", url="https://ghe/pr/2")
    con.execute(
        "INSERT INTO pr_flow VALUES (?,?,?,?,?)",
        ("platform/api#2", None, None, None, None),
    )
    items = flow.open_prs(con)
    assert items[0].review_wait_text == "reviewed"
