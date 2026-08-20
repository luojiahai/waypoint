from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.config import load_config
from waypoint.metrics import risks
from tests.factories import (
    insert_column, insert_issue, insert_person, insert_pr, insert_repo,
    insert_review, insert_transition, make_db
)

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def cfg(waypoint_root: Path):
    return load_config(waypoint_root)


@pytest.fixture
def con(tmp_path: Path):
    connection = make_db(tmp_path)
    insert_person(connection, "alex-rivera", "Alex Rivera", github_login="arivera")
    insert_person(connection, "bo-chen", "Bo Chen", github_login="bchen")
    insert_repo(connection, "platform/api")
    insert_column(connection, 0, "In Progress", 0, 2, ["10002"])
    insert_column(connection, 1, "Blocked", 1, None, ["10009"])
    return connection


def rules_of(register) -> list[str]:
    return [item.rule for item in register.items]


def test_pr_open_without_review_past_the_threshold(con, cfg):
    insert_pr(con, "platform/api#1", state="OPEN", ready_at="2026-08-14T12:00:00Z",
              url="https://ghe/pr/1", title="checkout")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)", ("platform/api#1", None, None, None, 120.0))
    register = risks.rule_risks(con, cfg, now=NOW)
    assert "pr_no_review" in rules_of(register)
    item = next(i for i in register.items if i.rule == "pr_no_review")
    assert item.evidence[0].url == "https://ghe/pr/1"
    assert item.age_text == "5d"


def test_a_fresh_unreviewed_pr_does_not_fire(con, cfg):
    insert_pr(con, "platform/api#1", state="OPEN", ready_at="2026-08-19T00:00:00Z")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)", ("platform/api#1", None, None, None, 12.0))
    assert rules_of(risks.rule_risks(con, cfg, now=NOW)) == []


def test_approved_but_unmerged_past_the_threshold(con, cfg):
    insert_pr(con, "platform/api#1", state="OPEN", ready_at="2026-08-10T12:00:00Z")
    insert_review(con, "r1", "platform/api#1", "bo-chen", state="APPROVED",
                  submitted_at="2026-08-14T12:00:00Z")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)", ("platform/api#1", 96.0, None, None, None))
    assert "pr_approved_unmerged" in rules_of(risks.rule_risks(con, cfg, now=NOW))


def test_issue_stalled_in_progress(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-01T12:00:00Z", None, None, "2026-08-05T12:00:00Z", 14.0))
    assert "issue_stalled" in rules_of(risks.rule_risks(con, cfg, now=NOW))


def test_a_stalled_issue_with_recent_linked_pr_activity_does_not_fire(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-01T12:00:00Z", None, None, "2026-08-05T12:00:00Z", 14.0))
    insert_pr(con, "platform/api#1", updated_at="2026-08-18T12:00:00Z", state="OPEN")
    con.execute("INSERT INTO issue_pr_links VALUES ('PROJ-1', 'platform/api#1')")
    assert "issue_stalled" not in rules_of(risks.rule_risks(con, cfg, now=NOW))


def test_flagged_issue_is_high(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002", flagged=1)
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    item = next(i for i in risks.rule_risks(con, cfg, now=NOW).items if i.rule == "issue_flagged")
    assert item.severity == "high"


def test_issue_in_a_wip_column_with_no_assignee_is_medium(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002",
                 assignee="unattributed")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    item = next(i for i in risks.rule_risks(con, cfg, now=NOW).items if i.rule == "issue_unassigned")
    assert item.severity == "med"


def test_column_over_its_limit_is_medium(con, cfg):
    for index in range(3):
        insert_issue(con, f"PROJ-{index}", status_category="In Progress", status_id="10002")
        con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                    (f"PROJ-{index}", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    item = next(i for i in risks.rule_risks(con, cfg, now=NOW).items if i.rule == "column_over_limit")
    assert item.severity == "med"
    assert "In Progress" in item.title


def test_column_over_limit_carries_the_real_age_of_the_oldest_item(con, cfg):
    # Override 2: the WIP-limit risk must carry a real age, not a hardcoded
    # 0.0/"" placeholder -- otherwise the register's severity-then-age sort is
    # meaningless for this rule and the UI's age column renders empty.
    insert_issue(con, "PROJ-0", status_category="In Progress", status_id="10002")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-0", "2026-08-01T12:00:00Z", None, None, "2026-08-01T12:00:00Z", 18.0))
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    insert_issue(con, "PROJ-2", status_category="In Progress", status_id="10002")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-2", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    item = next(i for i in risks.rule_risks(con, cfg, now=NOW).items if i.rule == "column_over_limit")
    assert item.age_days == 18.0
    assert item.age_text == "18d"


def test_column_over_limit_with_no_in_flight_items_reports_an_honest_unknown_age(con, cfg):
    # The column's count is driven by status_id membership, not status_category
    # (board.columns groups all jira_issues by status_id), so a column can be
    # over its limit purely on issues that never appear in board.in_flight()
    # (status_category != "In Progress"). That must not fabricate a 0.0/"0d".
    for index in range(3):
        insert_issue(con, f"PROJ-{index}", status_category="Done", status_id="10002")
    item = next(i for i in risks.rule_risks(con, cfg, now=NOW).items if i.rule == "column_over_limit")
    assert item.age_text == "—"
    assert item.age_days == 0.0


def test_a_column_with_no_limit_never_fires_the_limit_rule(con, cfg):
    for index in range(9):
        insert_issue(con, f"PROJ-{index}", status_category="In Progress", status_id="10009")
        con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                    (f"PROJ-{index}", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    assert "column_over_limit" not in rules_of(risks.rule_risks(con, cfg, now=NOW))


def test_issue_in_flight_past_the_aging_threshold(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-01T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    assert "issue_aging" in rules_of(risks.rule_risks(con, cfg, now=NOW))


def test_all_in_flight_children_of_an_epic_on_one_person(con, cfg):
    for index in range(2):
        key = f"PROJ-{index}"
        insert_issue(con, key, status_category="In Progress", status_id="10002",
                     parent_key="PROJ-10", assignee="alex-rivera")
        con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                    (key, "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    item = next(i for i in risks.rule_risks(con, cfg, now=NOW).items if i.rule == "epic_single_owner")
    assert item.severity == "med"
    assert "Alex Rivera" in item.detail


def test_issue_reopened_more_than_once(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002")
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    insert_transition(con, "PROJ-1", "2026-08-02T12:00:00Z", "Done", from_value="In Progress")
    insert_transition(con, "PROJ-1", "2026-08-03T12:00:00Z", "In Progress", from_value="Done")
    insert_transition(con, "PROJ-1", "2026-08-04T12:00:00Z", "Done", from_value="In Progress")
    insert_transition(con, "PROJ-1", "2026-08-05T12:00:00Z", "In Progress", from_value="Done")
    assert "issue_reopened" in rules_of(risks.rule_risks(con, cfg, now=NOW))


def test_an_epic_projected_past_its_due_date_escalates_with_drift(con, cfg):
    # Override 1: §11's missing tenth rule. PROJ-10 is an epic due 2026-08-25
    # whose trailing completion rate projects it well past that date.
    # status_category="To Do"/status_id="99999" for the epic and its children
    # keeps them out of board.in_flight() and out of the "In Progress" column's
    # WIP count, so this test isolates epic_drift from unrelated rules.
    insert_issue(con, "PROJ-10", summary="Checkout", type="Epic", status="To Do",
                 status_category="To Do", status_id="99999", parent_key=None,
                 labels=["due:2026-08-25"])
    for index in range(4):
        key = f"PROJ-2{index}"
        insert_issue(con, key, parent_key="PROJ-10", status="Done", status_category="Done",
                     status_id="99999", resolved_at="2026-08-12T09:00:00Z")
        con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                    (key, "2026-07-01T09:00:00Z", "2026-08-12T09:00:00Z", None,
                     "2026-08-12T09:00:00Z", 0.0))
    for index in range(8):
        key = f"PROJ-3{index}"
        insert_issue(con, key, parent_key="PROJ-10", status="To Do",
                     status_category="To Do", status_id="99999")
    register = risks.rule_risks(con, cfg, now=NOW)
    assert "epic_drift" in rules_of(register)
    item = next(i for i in register.items if i.rule == "epic_drift")
    assert item.evidence[0].ref == "PROJ-10"
    assert item.severity in {"low", "med", "high"}
    assert item.age_days > 0


def test_severity_escalates_with_age():
    assert risks.escalate(0.5) == "low"
    assert risks.escalate(1.5) == "med"
    assert risks.escalate(3.0) == "high"


def test_register_is_ordered_by_severity_then_age(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002", flagged=1)
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-18T12:00:00Z", None, None, "2026-08-18T12:00:00Z", 1.0))
    insert_pr(con, "platform/api#1", state="OPEN", ready_at="2026-08-16T12:00:00Z")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)", ("platform/api#1", None, None, None, 72.0))
    severities = [item.severity for item in risks.rule_risks(con, cfg, now=NOW).items]
    assert severities == sorted(severities, key=lambda s: {"high": 0, "med": 1, "low": 2}[s])


def test_an_empty_register_reports_how_many_rules_were_evaluated(con, cfg):
    # Override 3: `evaluated` counts each RULE once, not each row/loop
    # iteration. In this fixture two rules have a genuine candidate to check
    # even though nothing crosses a threshold: `pr_no_review` (the one open
    # PR) and `column_over_limit` (the `con` fixture's "In Progress" column
    # carries a configured WIP limit, so it is checked even at zero WIP) --
    # so the coherent per-rule count here is 2, not the brief's stated 1.
    insert_pr(con, "platform/api#1", state="OPEN", ready_at="2026-08-19T00:00:00Z")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)", ("platform/api#1", None, None, None, 6.0))
    register = risks.rule_risks(con, cfg, now=NOW)
    assert register.items == []
    assert register.evaluated == 2
    assert register.empty_message == "Nothing crossed a threshold."


def test_evaluated_does_not_double_count_repeated_candidates_for_one_rule(con, cfg):
    # The heart of Override 3: three open PRs all missing review should still
    # count `pr_no_review` once, not three times.
    for index in range(3):
        insert_pr(con, f"platform/api#{index}", state="OPEN", ready_at="2026-08-19T00:00:00Z")
        con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)",
                    (f"platform/api#{index}", None, None, None, 6.0))
    register = risks.rule_risks(con, cfg, now=NOW)
    assert register.evaluated == 2  # pr_no_review once + column_over_limit once


def test_every_risk_carries_evidence(con, cfg):
    insert_issue(con, "PROJ-1", status_category="In Progress", status_id="10002", flagged=1)
    con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                ("PROJ-1", "2026-08-01T12:00:00Z", None, None, "2026-08-02T12:00:00Z", 17.0))
    for item in risks.rule_risks(con, cfg, now=NOW).items:
        assert item.evidence
        assert item.evidence[0].ref
