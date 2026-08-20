import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.config import load_config
from waypoint.metrics import people
from waypoint.roster import Roster
from tests.factories import (
    insert_issue, insert_person, insert_pr, insert_repo, insert_review,
    insert_review_request, insert_transition, make_db
)

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
SINCE = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def cfg(waypoint_root: Path):
    return load_config(waypoint_root)


@pytest.fixture
def con(tmp_path: Path):
    connection = make_db(tmp_path)
    insert_person(connection, "alex-rivera", "Alex Rivera", github_login="arivera",
                  jira_account_id="acct-alex")
    insert_person(connection, "bo-chen", "Bo Chen", github_login="bchen",
                  jira_account_id="acct-bo")
    insert_repo(connection, "platform/api")
    return connection


def test_roster_cards_are_alphabetical_and_one_per_active_person(con, cfg):
    cards = people.roster_cards(con, Roster.from_config(cfg), now=NOW, thresholds=cfg.thresholds)
    assert [card.name for card in cards] == ["Alex Rivera", "Bo Chen"]
    assert cards[0].handle == "arivera · acct-alex"


def test_a_card_states_load_and_shape_never_output(con, cfg):
    insert_pr(con, "platform/api#1", author="alex-rivera", state="OPEN",
              ready_at="2026-08-17T12:00:00Z")
    insert_issue(con, "PROJ-1", assignee="alex-rivera", status_category="In Progress")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-10T12:00:00Z", None, None, "2026-08-16T12:00:00Z", 3.0))
    card = people.roster_cards(con, Roster.from_config(cfg), now=NOW,
                              thresholds=cfg.thresholds)[0]
    text = " ".join(line.text for line in card.lines)
    assert "1 open PR" in text
    assert "1 issue in flight" in text
    for forbidden in ("velocity", "throughput", "score", "rank", "points closed"):
        assert forbidden not in text.lower()


def test_a_card_is_flagged_when_a_figure_crossed_a_threshold(con, cfg):
    insert_pr(con, "platform/api#1", author="bo-chen", state="OPEN",
              ready_at="2026-08-10T12:00:00Z")
    insert_review_request(con, "platform/api#1", "alex-rivera", "2026-08-10T12:00:00Z")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)",
                ("platform/api#1", None, None, None, 216.0))
    cards = {c.name: c for c in people.roster_cards(con, Roster.from_config(cfg), now=NOW,
                                                   thresholds=cfg.thresholds)}
    assert cards["Alex Rivera"].flagged is True
    assert any(line.emphasis == "med" for line in cards["Alex Rivera"].lines)


def test_no_people_function_takes_a_sort_key_or_returns_a_ranking():
    for name, member in inspect.getmembers(people, inspect.isfunction):
        params = inspect.signature(member).parameters
        assert not any(p in {"sort", "sort_by", "order_by", "rank"} for p in params), name


def test_workstreams_touched_counts_distinct_epics_plus_repos(con, cfg):
    insert_issue(con, "PROJ-1", assignee="alex-rivera", parent_key="PROJ-10",
                 resolved_at="2026-08-10T12:00:00Z", status_category="Done")
    insert_issue(con, "PROJ-2", assignee="alex-rivera", parent_key="PROJ-10",
                 resolved_at="2026-08-11T12:00:00Z", status_category="Done")
    insert_issue(con, "PROJ-3", assignee="alex-rivera", parent_key="PROJ-20",
                 resolved_at="2026-08-12T12:00:00Z", status_category="Done")
    insert_pr(con, "platform/api#1", author="alex-rivera", merged_at="2026-08-09T12:00:00Z",
              state="MERGED")
    assert people.workstreams_touched(con, "alex-rivera", since=SINCE, until=NOW) == 3


def test_person_view_shipped_covers_merged_prs_and_resolved_issues(con, cfg):
    insert_pr(con, "platform/api#1", author="alex-rivera", state="MERGED",
              merged_at="2026-08-10T12:00:00Z", title="PROJ-1 thing")
    insert_issue(con, "PROJ-1", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-08-11T12:00:00Z")
    view = people.person_view(
        con, Roster.from_config(cfg).by_id("alex-rivera"), now=NOW, since=SINCE,
        work_mix=cfg.work_mix,
    )
    assert [item.ref for item in view.shipped.items] == ["PROJ-1", "platform/api#1"]


def test_person_view_states_an_empty_window_rather_than_a_zero(con, cfg):
    view = people.person_view(
        con, Roster.from_config(cfg).by_id("alex-rivera"), now=NOW, since=SINCE,
        work_mix=cfg.work_mix,
    )
    assert view.shipped.empty_message == "Nothing shipped in this window."
    assert view.shipped.items == []


def test_waiting_panels_separate_the_two_directions(con, cfg):
    insert_pr(con, "platform/api#1", author="alex-rivera", state="OPEN",
              ready_at="2026-08-15T12:00:00Z", title="mine")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)",
                ("platform/api#1", None, None, None, 96.0))
    insert_pr(con, "platform/api#2", number=2, author="bo-chen", state="OPEN",
              ready_at="2026-08-16T12:00:00Z", title="theirs")
    insert_review_request(con, "platform/api#2", "alex-rivera", "2026-08-16T12:00:00Z")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)",
                ("platform/api#2", None, None, None, 72.0))
    view = people.person_view(
        con, Roster.from_config(cfg).by_id("alex-rivera"), now=NOW, since=SINCE,
        work_mix=cfg.work_mix,
    )
    assert [item.ref for item in view.waiting_on_others.items] == ["platform/api#1"]
    assert [item.ref for item in view.others_waiting_on_them.items] == ["platform/api#2"]


def test_work_mix_is_prose_naming_the_issue_keys_in_each_bucket(con, cfg):
    insert_issue(con, "PROJ-97", type="Story", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-08-10T12:00:00Z")
    insert_issue(con, "PROJ-113", type="Bug", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-08-12T12:00:00Z")
    insert_issue(con, "PROJ-50", type="Story", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-07-25T12:00:00Z")
    view = people.person_view(
        con, Roster.from_config(cfg).by_id("alex-rivera"), now=NOW, since=SINCE,
        work_mix=cfg.work_mix,
    )
    buckets = {bucket.name: bucket for bucket in view.work_mix.buckets}
    assert buckets["feature"].keys == ["PROJ-97"]
    assert buckets["bug"].keys == ["PROJ-113"]
    assert "2 resolved in this window" in view.work_mix.prose
    assert "1 in the prior window of equal length" in view.work_mix.prose


def test_work_mix_returns_no_chart():
    source = inspect.getsource(people.person_view)
    assert "sparkline" not in source and "bar_spark" not in source


def test_unmapped_issue_types_land_in_other_with_their_count(con, cfg):
    insert_issue(con, "PROJ-1", type="Spike", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-08-10T12:00:00Z")
    view = people.person_view(
        con, Roster.from_config(cfg).by_id("alex-rivera"), now=NOW, since=SINCE,
        work_mix=cfg.work_mix,
    )
    other = next(b for b in view.work_mix.buckets if b.name == "other")
    assert other.count == 1
    assert other.keys == ["PROJ-1"]


def test_an_empty_other_bucket_is_omitted(con, cfg):
    insert_issue(con, "PROJ-1", type="Story", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-08-10T12:00:00Z")
    view = people.person_view(
        con, Roster.from_config(cfg).by_id("alex-rivera"), now=NOW, since=SINCE,
        work_mix=cfg.work_mix,
    )
    assert all(bucket.name != "other" for bucket in view.work_mix.buckets)
