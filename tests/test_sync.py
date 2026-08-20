from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.config import load_config
from waypoint.errors import WaypointError, SourceError
from waypoint.sources.base import EntityStatus, RawRecord
from waypoint.store.manifest import ManifestStore
from waypoint.sync import Progress, SyncLock, read_progress, run_sync

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


class FakeSource:
    def __init__(self, name, entities, records, statuses=None, explode=False):
        self.name = name
        self.entities = tuple(entities)
        self._records = records
        self._statuses = statuses or {
            entity: EntityStatus(entity, "ok", 0) for entity in entities
        }
        self._explode = explode
        self.seen_since = None

    def fetch(self, since):
        self.seen_since = dict(since)
        if self._explode:
            raise SourceError("everything is on fire", kind="server")
        yield from self._records

    def status(self):
        return self._statuses


def github_source(count=1, **kwargs):
    records = [
        RawRecord("github", "pull_requests", f"platform/api#{index}", "2026-08-19T09:00:00Z", {
            "number": index, "title": "t", "url": "u", "state": "OPEN", "isDraft": False,
            "body": "", "createdAt": "2026-08-14T10:00:00Z", "updatedAt": "2026-08-18T12:00:00Z",
            "mergedAt": None, "closedAt": None, "additions": 1, "deletions": 1, "changedFiles": 1,
            "baseRefName": "main", "headRefName": "b", "author": {"login": "arivera"},
            "labels": {"nodes": []}, "commits": {"nodes": []},
            "timelineItems": {"nodes": []}, "reviews": {"nodes": []},
        })
        for index in range(1, count + 1)
    ]
    statuses = {
        "pull_requests": EntityStatus("pull_requests", "ok", count, watermark="2026-08-18T12:00:00Z"),
        "reviews": EntityStatus("reviews", "ok", 0),
        "review_requests": EntityStatus("review_requests", "ok", 0),
    }
    return FakeSource("github", tuple(statuses), records, statuses, **kwargs)


def test_sync_writes_raw_builds_the_index_and_records_the_manifest(project_dir: Path):
    progress = run_sync(project_dir, now=NOW, sources=[github_source(count=2)])
    root = project_dir / ".waypoint"
    assert progress.state == "done"
    assert (root / "raw" / "github" / "pull_requests").exists()
    assert (root / "index.db").exists()
    manifest = ManifestStore(root).load()
    assert manifest.entities["github/pull_requests"].count == 2
    assert manifest.entities["github/pull_requests"].watermark == "2026-08-18T12:00:00Z"


def test_the_next_sync_passes_the_watermark_back_to_the_source(project_dir: Path):
    run_sync(project_dir, now=NOW, sources=[github_source()])
    second = github_source()
    run_sync(project_dir, now=NOW, sources=[second])
    assert second.seen_since == {
        "pull_requests": "2026-08-18T12:00:00Z", "reviews": None, "review_requests": None
    }


def test_a_source_that_raises_records_failed_without_stopping_the_run(project_dir: Path):
    exploder = FakeSource("jira", ("issues",), [], explode=True)
    progress = run_sync(project_dir, now=NOW, sources=[github_source(), exploder])
    manifest = ManifestStore(project_dir / ".waypoint").load()
    assert manifest.entities["github/pull_requests"].status == "ok"
    assert manifest.entities["jira/issues"].status == "failed"
    assert "on fire" in manifest.entities["jira/issues"].error
    assert progress.state == "done"


def test_progress_is_written_and_readable_during_and_after_a_run(project_dir: Path):
    run_sync(project_dir, now=NOW, sources=[github_source()])
    progress = read_progress(project_dir / ".waypoint")
    assert progress.state == "done"
    assert progress.finished_at == "2026-08-19T12:00:00Z"
    assert progress.counts["github/pull_requests"] == 1


def test_progress_before_any_sync_is_idle(project_dir: Path):
    assert read_progress(project_dir / ".waypoint").state == "idle"


def test_a_second_concurrent_sync_is_refused(project_dir: Path):
    root = project_dir / ".waypoint"
    with SyncLock(root):
        with pytest.raises(WaypointError) as exc:
            with SyncLock(root):
                pass
    assert "already running" in exc.value.message


def test_a_lock_left_by_a_dead_process_is_reclaimed(project_dir: Path):
    root = project_dir / ".waypoint"
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "state" / "sync.lock").write_text('{"pid": 999999, "started_at": "x"}')
    with SyncLock(root):
        pass  # no exception


def test_the_lock_is_released_even_when_the_body_raises(project_dir: Path):
    root = project_dir / ".waypoint"
    with pytest.raises(RuntimeError):
        with SyncLock(root):
            raise RuntimeError("boom")
    with SyncLock(root):
        pass


def test_sync_run_is_recorded_in_the_index(project_dir: Path):
    run_sync(project_dir, now=NOW, sources=[github_source()])
    from waypoint.store.index import connect

    con = connect(project_dir / ".waypoint" / "index.db", read_only=True)
    row = con.execute("SELECT * FROM sync_runs").fetchone()
    assert row["status"] == "ok"
    assert row["manifest_digest"].startswith("sha256:")


def test_sync_writes_the_waypoint_gitignore(project_dir: Path):
    run_sync(project_dir, now=NOW, sources=[github_source()])
    text = (project_dir / ".waypoint" / ".gitignore").read_text()
    assert "raw/" in text
    assert "index.db" in text
    assert "state/" in text
    assert "reports/" not in text  # reports and config stay committable (§7)


def test_an_existing_gitignore_is_not_overwritten(project_dir: Path):
    path = project_dir / ".waypoint" / ".gitignore"
    path.write_text("# mine\nraw/\n")
    run_sync(project_dir, now=NOW, sources=[github_source()])
    assert path.read_text() == "# mine\nraw/\n"


def test_rate_limit_state_is_recorded_from_sources_that_expose_it(project_dir: Path):
    from waypoint.sources.http import RateLimitState

    class WithLimits(FakeSource):
        class _Http:
            rate_limit = RateLimitState(remaining=412, reset_at="2026-08-19T13:00:00Z",
                                        waited_seconds=7.0)

        http = _Http()

    source = WithLimits("github", ("pull_requests",), [],
                        {"pull_requests": EntityStatus("pull_requests", "ok", 0)})
    run_sync(project_dir, now=NOW, sources=[source])
    progress = read_progress(project_dir / ".waypoint")
    assert progress.rate_limit["github"]["remaining"] == 412
    assert progress.rate_limit["github"]["waited_seconds"] == 7.0


def test_a_malformed_progress_file_reads_as_a_failure_that_names_it(tmp_path: Path):
    """`Progress(**json.loads(...))` turned a truncated write -- a file
    Waypoint itself produced -- into a TypeError/JSONDecodeError out of every
    page's dependency."""
    path = tmp_path / "state" / "progress.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"state": "running", "step":')
    progress = read_progress(tmp_path)
    assert progress.state == "failed"
    assert str(path) in progress.message
    assert "press Sync" in progress.message


def test_an_unknown_progress_key_is_caught_too(tmp_path: Path):
    path = tmp_path / "state" / "progress.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"state": "idle", "from_a_future_version": true}')
    assert read_progress(tmp_path).state == "failed"


def test_progress_is_written_atomically_so_a_kill_cannot_truncate_it(tmp_path, monkeypatch):
    """`ManifestStore.save` already renames a `.tmp` into place; a bare
    `write_text` here left a truncated file that bricked the whole UI."""
    from waypoint.sync import write_progress

    renamed: list[str] = []
    real_replace = Path.replace
    monkeypatch.setattr(
        Path, "replace",
        lambda self, target: (renamed.append(self.name), real_replace(self, target))[1],
    )

    write_progress(tmp_path, Progress(state="running", step="fetch github"))

    assert renamed == ["progress.json.tmp"]
    assert not (tmp_path / "state" / "progress.json.tmp").exists()
    assert read_progress(tmp_path).step == "fetch github"
