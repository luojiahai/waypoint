from pathlib import Path

from fastapi.testclient import TestClient

from waypoint.sources.base import EntityStatus
from waypoint.store.manifest import ManifestStore
from waypoint.web.app import create_app
from tests.factories import insert_person, make_db


def seed(project_dir: Path, *, unattributed=()):
    root = project_dir / ".waypoint"
    con = make_db(root)
    insert_person(con, "alex-rivera", "Alex Rivera", github_login="arivera")
    for row in unattributed:
        con.execute("INSERT INTO unattributed VALUES (?,?,?,?)", row)
    con.commit()
    con.close()
    store = ManifestStore(root)
    manifest = store.load()
    manifest.record("github", {
        "pull_requests": EntityStatus("pull_requests", "ok", 12, watermark="2026-08-18T12:00:00Z"),
        "reviews": EntityStatus("reviews", "partial", 3, error="secondary rate limit at page 4",
                                watermark="2026-08-17T12:00:00Z"),
        "review_requests": EntityStatus("review_requests", "ok", 2),
    }, "r1", "2026-08-19T09:12:03Z")
    manifest.record("jira", {
        "issues": EntityStatus("issues", "failed", 0, error="401 authentication failed"),
        "changelogs": EntityStatus("changelogs", "failed", 0, error="401 authentication failed"),
        "board_config": EntityStatus("board_config", "ok", 1),
    }, "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    return TestClient(create_app(project_dir))


def test_sync_page_lists_one_row_per_source_entity(project_dir: Path):
    body = seed(project_dir).get("/sync").text
    for key in ("github/pull_requests", "github/reviews", "jira/issues", "jira/board_config"):
        assert key in body


def test_counts_and_statuses_are_shown(project_dir: Path):
    body = seed(project_dir).get("/sync").text
    assert "12" in body
    assert "PARTIAL" in body
    assert "FAILED" in body


def test_real_error_text_and_the_resume_watermark_are_shown(project_dir: Path):
    body = seed(project_dir).get("/sync").text
    assert "secondary rate limit at page 4" in body
    assert "401 authentication failed" in body
    assert "2026-08-17T12:00:00Z" in body


def test_unrostered_identities_are_listed_with_a_suggested_fix(project_dir: Path):
    body = seed(project_dir, unattributed=[("github", "dependabot", "author", 14)]).get("/sync").text
    assert "dependabot" in body
    assert "github.bot_logins" in body


def test_no_unrostered_identities_is_a_legible_result(project_dir: Path):
    body = seed(project_dir).get("/sync").text
    assert "Every identity in the data is in the roster." in body


def test_rate_limit_state_from_the_last_run_is_shown(project_dir: Path):
    from waypoint.sync import Progress, write_progress

    client = seed(project_dir)
    write_progress(project_dir / ".waypoint", Progress(
        state="done", step="complete", finished_at="2026-08-19T09:12:03Z",
        rate_limit={"github": {"remaining": 412, "reset_at": "2026-08-19T13:00:00Z",
                               "waited_seconds": 7.0}},
    ))
    body = client.get("/sync").text
    assert "412 remaining" in body
    assert "waited 7.0s" in body


def test_no_rate_limit_headers_is_a_legible_result(project_dir: Path):
    body = seed(project_dir).get("/sync").text
    assert "No rate-limit headers seen on the last run." in body


def test_posting_sync_returns_the_running_partial(project_dir: Path, monkeypatch):
    import waypoint.web.routes.sync as sync_route

    def fake_run_sync(project_dir, *, now, sources=None, cfg=None):
        # Stand in for the real sync so the background task never opens a socket.
        return None

    monkeypatch.setattr(sync_route, "run_sync", fake_run_sync)

    client = seed(project_dir)
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    response = client.post("/sync")
    assert response.status_code == 200
    assert "hx-get" in response.text
    assert "/sync/status" in response.text


def test_posting_sync_without_credentials_reports_it_instead_of_starting(project_dir: Path, monkeypatch):
    for name in ("WAYPOINT_GITHUB_TOKEN", "WAYPOINT_JIRA_EMAIL", "WAYPOINT_JIRA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    response = seed(project_dir).post("/sync")
    assert "WAYPOINT_GITHUB_TOKEN" in response.text


def test_status_endpoint_returns_the_partial(project_dir: Path):
    response = seed(project_dir).get("/sync/status")
    assert response.status_code == 200
    assert "sync-state" in response.text


def test_the_partial_stops_polling_once_the_run_is_done(project_dir: Path):
    from waypoint.sync import Progress, write_progress

    client = seed(project_dir)
    write_progress(project_dir / ".waypoint", Progress(state="done", step="complete",
                                                       finished_at="2026-08-19T09:12:03Z"))
    body = client.get("/sync/status").text
    assert "hx-trigger" not in body


def test_a_second_post_while_running_does_not_start_another(project_dir: Path, monkeypatch):
    from waypoint.sync import SyncLock

    client = seed(project_dir)
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    with SyncLock(project_dir / ".waypoint"):
        response = client.post("/sync")
    assert "already running" in response.text


def test_a_lock_left_by_a_dead_process_does_not_block_a_new_sync(project_dir: Path, monkeypatch):
    import waypoint.web.routes.sync as sync_route

    def fake_run_sync(project_dir, *, now, sources=None, cfg=None):
        # Stand in for the real sync so the background task never opens a socket.
        return None

    monkeypatch.setattr(sync_route, "run_sync", fake_run_sync)

    client = seed(project_dir)
    root = project_dir / ".waypoint"
    (root / "state").mkdir(parents=True, exist_ok=True)
    # A pid this high is not a real running process on the test machine — the
    # same convention tests/test_sync.py uses for "a dead process held this".
    (root / "state" / "sync.lock").write_text('{"pid": 999999, "started_at": "x"}')

    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    response = client.post("/sync")
    assert "already running" not in response.text
    assert "hx-get" in response.text
    assert "/sync/status" in response.text


def test_an_unexpected_error_in_the_background_task_reports_failed_not_running(
    project_dir: Path, monkeypatch
):
    import waypoint.web.routes.sync as sync_route
    from waypoint.sync import read_progress

    def boom(project_dir, *, now, sources=None, cfg=None):
        raise RuntimeError("boom with a fake secret abc123")

    monkeypatch.setattr(sync_route, "run_sync", boom)

    client = seed(project_dir)
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "e@example.com")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "t")
    client.post("/sync")

    progress = read_progress(project_dir / ".waypoint")
    assert progress.state == "failed"
    assert "abc123" not in progress.message

    body = client.get("/sync/status").text
    assert "hx-get" not in body
    assert "abc123" not in body


PAGES = ("/", "/delivery", "/people", "/sync")


def test_a_corrupt_manifest_leaves_every_page_rendering_and_says_what_broke(project_dir: Path):
    """UI§9: a missing or broken file must not 500 -- least of all the Sync
    page, which is the one that would have explained it."""
    client = seed(project_dir)
    manifest_path = project_dir / ".waypoint" / "state" / "manifest.json"
    manifest_path.write_text(manifest_path.read_text()[: len(manifest_path.read_text()) // 2])

    for path in PAGES:
        response = client.get(path)
        assert response.status_code < 500, path
        assert "manifest.json" in response.text, path
        assert "press Sync" in response.text, path


def test_a_malformed_progress_file_leaves_every_page_rendering(project_dir: Path):
    client = seed(project_dir)
    progress_path = project_dir / ".waypoint" / "state" / "progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text('{"state": "running", "step":')

    for path in PAGES:
        response = client.get(path)
        assert response.status_code < 500, path
        assert "progress.json" in response.text, path
        assert "press Sync" in response.text, path
