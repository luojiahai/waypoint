import re
from pathlib import Path

from fastapi.testclient import TestClient

from waypoint.sources.base import EntityStatus
from waypoint.store.manifest import ManifestStore
from waypoint.web.app import create_app
from tests.factories import insert_issue, insert_person, insert_pr, insert_repo, make_db


def seed(project_dir: Path):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_person(con, "alex-rivera", "Alex Rivera", github_login="arivera",
                  jira_account_id="acct-alex")
    insert_person(con, "bo-chen", "Bo Chen", github_login="bchen", jira_account_id="acct-bo")
    insert_repo(con, "platform/api")
    insert_pr(con, "platform/api#1", author="alex-rivera", state="MERGED",
              merged_at="2026-08-10T12:00:00Z", title="PROJ-97 checkout", url="https://ghe/pr/1")
    insert_issue(con, "PROJ-97", type="Story", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-08-11T12:00:00Z", summary="Checkout rework")
    insert_issue(con, "PROJ-113", type="Bug", assignee="alex-rivera", status_category="Done",
                 resolved_at="2026-08-12T12:00:00Z", summary="Rounding")
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


def test_roster_renders_a_card_per_person_in_a_three_column_grid(project_dir: Path):
    body = seed(project_dir).get("/people").text
    assert body.count('class="card') == 2
    assert "grid-3" in body


def test_the_roster_contains_no_table(project_dir: Path):
    body = seed(project_dir).get("/people").text
    for forbidden in ("<table", "<thead", "<th", "<td"):
        assert forbidden not in body


def test_cards_are_alphabetical(project_dir: Path):
    body = seed(project_dir).get("/people").text
    assert body.index("Alex Rivera") < body.index("Bo Chen")


def test_the_standing_note_is_present_on_the_roster(project_dir: Path):
    body = seed(project_dir).get("/people").text
    assert "signals to ask about, not measures of performance" in body


def test_each_card_links_to_the_person_page(project_dir: Path):
    body = seed(project_dir).get("/people").text
    assert 'href="/people/alex-rivera"' in body


def test_person_page_carries_the_standing_note_and_the_handles(project_dir: Path):
    body = seed(project_dir).get("/people/alex-rivera?since=2026-08-05").text
    assert "signals to ask about, not measures of performance" in body
    assert "arivera" in body
    assert "acct-alex" in body


def test_person_page_has_four_panels(project_dir: Path):
    body = seed(project_dir).get("/people/alex-rivera?since=2026-08-05").text
    for label in ("Shipped", "In flight", "Waiting on someone else",
                  "Someone else waiting on them"):
        assert label in body


def test_work_mix_is_prose_with_the_issue_keys_named(project_dir: Path):
    body = seed(project_dir).get("/people/alex-rivera?since=2026-08-05").text
    assert "resolved in this window" in body
    assert "PROJ-97" in body
    assert "PROJ-113" in body


def test_work_mix_renders_no_chart(project_dir: Path):
    body = seed(project_dir).get("/people/alex-rivera?since=2026-08-05").text
    mix = body[body.index("work mix"):]
    assert "<svg" not in mix


def test_the_window_defaults_to_the_last_time_the_page_was_opened(project_dir: Path):
    client = seed(project_dir)
    client.get("/people/alex-rivera")
    from waypoint.store.views import PersonViews

    remembered = PersonViews(project_dir / ".waypoint").last_viewed("alex-rivera")
    assert remembered is not None

    # A second visit (still no ?since=) must default the window to the
    # remembered last-viewed time, not to the hardcoded 14-day fallback.
    # If the route ignored the stored window, this would render the
    # fallback's date instead and the assertion below would fail.
    body = client.get("/people/alex-rivera").text
    assert f'value="{remembered.date().isoformat()}"' in body


def test_an_inactive_window_states_it_rather_than_rendering_zeroes(project_dir: Path):
    body = seed(project_dir).get("/people/bo-chen?since=2026-08-05").text
    assert "Nothing shipped in this window." in body
    assert not re.search(r">\s*0\s*<", body)


def test_an_unknown_person_is_a_404(project_dir: Path):
    assert seed(project_dir).get("/people/nobody").status_code == 404
