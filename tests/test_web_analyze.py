import json
from pathlib import Path

from fastapi.testclient import TestClient

from waypoint import clock
from waypoint import skills_runner
from waypoint.sources.base import EntityStatus
from waypoint.store.manifest import ManifestStore
from waypoint.store.reports import ReportStore
from waypoint.web.app import create_app
from tests.factories import insert_person, make_db

SIDECAR = {
    "skill": "waypoint:delivery-risk",
    "generated_at": "2026-08-19T09:40:00Z",
    "window": {"from": "2026-08-05", "to": "2026-08-19"},
    "inputs_digest": "sha256:abc",
    "items": [{
        "severity": "high", "title": "Checkout has no reviewer coverage", "body": "B",
        "evidence": [{"type": "pull_request", "ref": "PR #482", "url": "https://ghe/pr/482"}],
        "question": "Who can back up Alex?",
    }],
}


def seed(project_dir: Path, digest_matches=False):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_person(con, "alex-rivera", "Alex Rivera", github_login="arivera")
    con.commit()
    con.close()
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("github", {e: EntityStatus(e, "ok", 1)
                               for e in ("pull_requests", "reviews", "review_requests")},
                    "r1", "2026-08-19T09:12:03Z")
    # Jira has to be recorded too, or the register is FAILED for a missing
    # entity and staleness is not what these tests are isolating: the register
    # panel now shows the *worst* of the data status and the report status.
    manifest.record("jira", {e: EntityStatus(e, "ok", 1)
                             for e in ("issues", "changelogs", "board_config")},
                    "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    sidecar = json.loads(json.dumps(SIDECAR))
    if digest_matches:
        sidecar["inputs_digest"] = manifest.digest()

    ReportStore(root).write("waypoint:delivery-risk", sidecar, "# Delivery risk\n", at=clock.now())
    return TestClient(create_app(project_dir))


def test_a_skill_backed_row_is_merged_into_the_register_with_the_skill_badge(project_dir: Path):
    body = seed(project_dir, digest_matches=True).get("/").text
    assert "Checkout has no reviewer coverage" in body
    assert "SKILL" in body


def test_a_report_predating_the_current_sync_is_demoted_to_stale(project_dir: Path):
    body = seed(project_dir, digest_matches=False).get("/").text
    assert "STALE" in body
    assert "changed since" in body


def test_a_panel_with_no_report_shows_the_analyze_button_and_what_it_produces(
    project_dir: Path, monkeypatch
):
    # Same determinism issue Override 2 calls out for the button-count test:
    # the Analyze button only renders when claude_available() is true, so
    # pin it rather than letting the assertion depend on whether the
    # machine running the tests happens to have Claude Code on PATH.
    monkeypatch.setattr(skills_runner, "claude_available", lambda runner="claude": True)
    root = project_dir / ".waypoint"
    con = make_db(root)
    con.commit()
    con.close()
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("github", {e: EntityStatus(e, "ok", 0)
                               for e in ("pull_requests", "reviews", "review_requests")},
                    "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    body = TestClient(create_app(project_dir)).get("/").text
    assert "Analyze" in body
    assert "Ranked risk register" in body


def test_analyze_returns_a_polling_partial(project_dir: Path, monkeypatch):
    # Override 1: never let this test spawn a real `claude` process. Force
    # claude_available() to a deterministic True (so behaviour doesn't depend
    # on whether the developer's machine has Claude Code installed), and
    # replace run_skill itself so no subprocess is ever created.
    monkeypatch.setattr(skills_runner, "claude_available", lambda runner="claude": True)
    monkeypatch.setattr(
        skills_runner, "run_skill",
        lambda *args, **kwargs: skills_runner.RunOutcome(True, "complete", None),
    )
    response = seed(project_dir).post("/analyze/delivery-risk")
    assert response.status_code == 200
    assert "/analyze/delivery-risk/status" in response.text


def test_an_unknown_skill_slug_is_a_404(project_dir: Path):
    assert seed(project_dir).post("/analyze/rm-rf").status_code == 404


def test_when_claude_is_absent_the_strip_says_so_and_nothing_else_breaks(project_dir: Path, monkeypatch):
    monkeypatch.setattr("waypoint.skills_runner.claude_available", lambda runner="claude": False)
    body = seed(project_dir, digest_matches=True).get("/").text
    assert "Generated analysis is unavailable" in body
    assert "Checkout has no reviewer coverage" in body  # the stored report still renders


def test_a_malformed_sidecar_renders_as_a_link_to_its_markdown(project_dir: Path):
    client = seed(project_dir, digest_matches=True)
    root = project_dir / ".waypoint"
    for path in (root / "reports").glob("*.json"):
        path.write_text("{not json")
    body = client.get("/").text
    assert "report could not be read" in body
    assert ".md" in body
