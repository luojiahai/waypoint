from pathlib import Path

from waypoint.sources.base import EntityStatus
from waypoint.store.manifest import Manifest, ManifestStore


def test_empty_manifest_loads_from_a_fresh_directory(tmp_path: Path):
    manifest = ManifestStore(tmp_path).load()
    assert manifest.entities == {}
    assert manifest.runs == []
    assert manifest.last_run() is None


def test_record_then_save_then_load_round_trips(tmp_path: Path):
    store = ManifestStore(tmp_path)
    manifest = store.load()
    manifest.record(
        "github",
        {
            "pull_requests": EntityStatus("pull_requests", "ok", 12, watermark="2026-08-19T09:00:00Z"),
            "reviews": EntityStatus("reviews", "partial", 3, error="rate limited at page 4"),
        },
        run_id="2026-08-19T09-12-03Z",
        at="2026-08-19T09:12:03Z",
    )
    store.save(manifest)

    reloaded = ManifestStore(tmp_path).load()
    assert reloaded.entities["github/pull_requests"].count == 12
    assert reloaded.entities["github/pull_requests"].watermark == "2026-08-19T09:00:00Z"
    assert reloaded.entities["github/reviews"].status == "partial"
    assert reloaded.entities["github/reviews"].error == "rate limited at page 4"
    assert reloaded.last_run().id == "2026-08-19T09-12-03Z"


def test_status_for_returns_the_worst_status_across_keys(tmp_path: Path):
    manifest = ManifestStore(tmp_path).load()
    manifest.record(
        "github",
        {
            "pull_requests": EntityStatus("pull_requests", "ok", 1),
            "reviews": EntityStatus("reviews", "partial", 1),
            "review_requests": EntityStatus("review_requests", "failed", 0, error="401"),
        },
        run_id="r1",
        at="2026-08-19T09:12:03Z",
    )
    assert manifest.status_for(["github/pull_requests"]) == "ok"
    assert manifest.status_for(["github/pull_requests", "github/reviews"]) == "partial"
    assert manifest.status_for(["github/reviews", "github/review_requests"]) == "failed"


def test_an_entity_never_synced_counts_as_failed(tmp_path: Path):
    manifest = ManifestStore(tmp_path).load()
    assert manifest.status_for(["jira/issues"]) == "failed"


def test_digest_is_stable_for_identical_state_and_changes_with_it(tmp_path: Path):
    def build(count: int) -> Manifest:
        m = ManifestStore(tmp_path).load()
        m.record(
            "github",
            {"pull_requests": EntityStatus("pull_requests", "ok", count)},
            run_id="r1",
            at="2026-08-19T09:12:03Z",
        )
        return m

    assert build(5).digest() == build(5).digest()
    assert build(5).digest() != build(6).digest()
    assert build(5).digest().startswith("sha256:")


def test_digest_ignores_run_history(tmp_path: Path):
    store = ManifestStore(tmp_path)
    manifest = store.load()
    manifest.record(
        "github", {"pull_requests": EntityStatus("pull_requests", "ok", 5)}, "r1", "2026-08-19T09:12:03Z"
    )
    before = manifest.digest()
    manifest.record(
        "github", {"pull_requests": EntityStatus("pull_requests", "ok", 5)}, "r2", "2026-08-20T09:12:03Z"
    )
    assert manifest.digest() == before


def test_only_the_twenty_most_recent_runs_are_retained(tmp_path: Path):
    manifest = ManifestStore(tmp_path).load()
    for index in range(25):
        manifest.record(
            "github",
            {"pull_requests": EntityStatus("pull_requests", "ok", index)},
            run_id=f"r{index}",
            at="2026-08-19T09:12:03Z",
        )
    assert len(manifest.runs) == 20
    assert manifest.runs[-1].id == "r24"


def test_manifest_file_contains_no_token_like_values(tmp_path: Path):
    store = ManifestStore(tmp_path)
    manifest = store.load()
    manifest.record(
        "github", {"pull_requests": EntityStatus("pull_requests", "ok", 1)}, "r1", "2026-08-19T09:12:03Z"
    )
    store.save(manifest)
    text = (tmp_path / "state" / "manifest.json").read_text().lower()
    assert "token" not in text
