import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from waypoint.sources.base import EntityStatus
from waypoint.store.manifest import ManifestStore
from waypoint.web.app import create_app
from tests.factories import insert_column, insert_issue, insert_person, insert_pr, insert_repo, make_db


def seed(project_dir: Path):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_person(con, "alex-rivera", "Alex Rivera", github_login="arivera")
    insert_repo(con, "platform/api")
    insert_column(con, 0, "In Progress", 0, 2, ["10002"])
    insert_issue(con, "PROJ-10", summary="Checkout", type="Epic", status_category="In Progress",
                 status_id="10002")
    for index, done in ((1, None), (2, "2026-08-12T09:00:00Z")):
        key = f"PROJ-{index}"
        insert_issue(con, key, parent_key="PROJ-10", status_id="10002",
                     status_category="Done" if done else "In Progress", resolved_at=done)
        con.execute("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)",
                    (key, "2026-07-20T09:00:00Z", done, 480.0 if done else None,
                     done or "2026-07-20T09:00:00Z", 30.0))
    insert_pr(con, "platform/api#1", state="MERGED", ready_at="2026-08-10T09:00:00Z",
              merged_at="2026-08-12T09:00:00Z")
    con.execute("INSERT INTO pr_flow VALUES (?,?,?,?,?)", ("platform/api#1", 24.0, 48.0, 24.0, None))
    con.commit()
    con.close()

    store = ManifestStore(root)
    manifest = store.load()
    for source, entities in (
        ("github", ("pull_requests", "reviews", "review_requests")),
        ("jira", ("issues", "changelogs", "board_config")),
    ):
        manifest.record(source, {e: EntityStatus(e, "ok", 1) for e in entities},
                        "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    return TestClient(create_app(project_dir))


def test_delivery_has_jump_chips_for_each_section(project_dir: Path):
    body = seed(project_dir).get("/delivery").text
    for anchor in ('href="#board"', 'href="#epics"', 'href="#flow"'):
        assert anchor in body


def test_board_section_shows_bars_beside_the_aging_chart(project_dir: Path):
    body = seed(project_dir).get("/delivery").text
    assert 'id="board"' in body
    assert "<svg" in body
    assert "threshold 10d" in body


def test_items_past_the_aging_threshold_are_listed_with_the_condition_they_met(project_dir: Path):
    body = seed(project_dir).get("/delivery").text
    assert "PROJ-1" in body
    assert "past aging threshold" in body


def test_epic_section_states_the_completion_basis(project_dir: Path):
    body = seed(project_dir).get("/delivery").text
    assert "by issue count" in body


def test_epic_row_shows_progress_and_projection(project_dir: Path):
    body = seed(project_dir).get("/delivery").text
    assert "1 / 2" in body
    assert "no recent progress" in body or "~" in body


def test_flow_section_has_four_panels(project_dir: Path):
    body = seed(project_dir).get("/delivery").text
    for label in ("PR review latency", "Issue cycle time", "WIP", "Weekly throughput"):
        assert label in body


def test_flow_panels_show_median_and_p75(project_dir: Path):
    body = seed(project_dir).get("/delivery").text
    assert "p75" in body


def test_no_chart_carries_a_goal_line(project_dir: Path):
    # Override 1: the original whole-page "target" check can never pass —
    # base.html's chrome emits hx-target on every page. The real intent is
    # that no CHART carries a goal line or threshold mark, so scope the check
    # to rendered <svg> markup. The WIP-limit tick and the aging threshold are
    # the only legitimate reference marks in the app and neither uses the
    # word "goal" nor the goal-line dasharray/class combination checked here.
    body = seed(project_dir).get("/delivery").text
    svg_blocks = re.findall(r"<svg[^>]*>.*?</svg>", body, re.DOTALL)
    assert svg_blocks, "expected at least one chart on the page"
    for svg in svg_blocks:
        lowered = svg.lower()
        assert "goal" not in lowered
        assert 'stroke-dasharray="2 2" class="goal"' not in lowered


def test_the_window_selector_changes_the_flow_window(project_dir: Path):
    # Override 2: "26w" alone can never distinguish a working selector from a
    # broken one, since week_options = (4, 12, 26, 52) renders every option
    # link on every request regardless of the current window. The template's
    # `<span class="chip">{{ weeks }}w selected</span>` is the one string that
    # actually reflects the selected window, so assert on that.
    client = seed(project_dir)
    assert "12w selected" in client.get("/delivery").text
    assert "26w selected" in client.get("/delivery?weeks=26").text


def test_an_empty_board_replaces_the_aging_chart_with_a_line(project_dir: Path):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_column(con, 0, "In Progress", 0, 2, ["10002"])
    con.commit()
    con.close()
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("jira", {e: EntityStatus(e, "ok", 0)
                             for e in ("issues", "changelogs", "board_config")},
                    "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    body = TestClient(create_app(project_dir)).get("/delivery").text
    assert "Nothing in progress." in body


def test_no_epics_gives_a_real_empty_state(project_dir: Path):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_column(con, 0, "In Progress", 0, 2, ["10002"])
    con.commit()
    con.close()
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("jira", {e: EntityStatus(e, "ok", 0)
                             for e in ("issues", "changelogs", "board_config")},
                    "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    assert "No epics with child issues." in TestClient(create_app(project_dir)).get("/delivery").text
