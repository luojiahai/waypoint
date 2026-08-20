from pathlib import Path

from waypoint.metrics.status import (
    BOARD, GITHUB_PRS, JIRA_ISSUES, DataStatus, panel_status, stale_status
)
from waypoint.sources.base import EntityStatus
from waypoint.store.manifest import ManifestStore


def manifest_with(tmp_path: Path, statuses: dict[str, tuple[str, str, int, str | None]]):
    manifest = ManifestStore(tmp_path).load()
    grouped: dict[str, dict[str, EntityStatus]] = {}
    for key, (source, state, count, error) in statuses.items():
        grouped.setdefault(source, {})[key] = EntityStatus(key, state, count, error=error)
    for source, entities in grouped.items():
        manifest.record(source, entities, "r1", "2026-08-19T09:12:03Z")
    return manifest


def test_all_ok_is_not_demoted(tmp_path: Path):
    manifest = manifest_with(tmp_path, {
        "pull_requests": ("github", "ok", 12, None),
        "reviews": ("github", "ok", 5, None),
        "review_requests": ("github", "ok", 2, None),
    })
    status = panel_status(manifest, GITHUB_PRS)
    assert status.state == "ok"
    assert status.badge is None
    assert status.demoted is False


def test_partial_badge_reason_names_the_count_the_cause_and_the_fix(tmp_path: Path):
    manifest = manifest_with(tmp_path, {
        "pull_requests": ("github", "partial", 62, "rate limited at page 4"),
        "reviews": ("github", "ok", 5, None),
        "review_requests": ("github", "ok", 2, None),
    })
    status = panel_status(manifest, GITHUB_PRS)
    assert status.state == "partial"
    assert status.badge == "PARTIAL"
    assert status.demoted is True
    assert "62" in status.reason
    assert "rate limited at page 4" in status.reason
    assert "Sync again" in status.reason


def test_failed_reason_names_the_source_the_error_and_what_is_unaffected(tmp_path: Path):
    manifest = manifest_with(tmp_path, {
        "issues": ("jira", "failed", 0, "401 authentication failed"),
        "changelogs": ("jira", "failed", 0, "401 authentication failed"),
    })
    status = panel_status(manifest, JIRA_ISSUES)
    assert status.badge == "FAILED"
    assert "jira" in status.reason
    assert "401 authentication failed" in status.reason
    assert "GitHub panels are unaffected" in status.reason


def test_worst_status_wins_across_a_panels_entities(tmp_path: Path):
    manifest = manifest_with(tmp_path, {
        "issues": ("jira", "partial", 40, "timeout"),
        "changelogs": ("jira", "failed", 0, "timeout"),
    })
    assert panel_status(manifest, JIRA_ISSUES).state == "failed"


def test_a_jira_failure_never_demotes_a_github_panel(tmp_path: Path):
    manifest = manifest_with(tmp_path, {
        "pull_requests": ("github", "ok", 12, None),
        "reviews": ("github", "ok", 5, None),
        "review_requests": ("github", "ok", 2, None),
        "issues": ("jira", "failed", 0, "boom"),
        "changelogs": ("jira", "failed", 0, "boom"),
    })
    assert panel_status(manifest, GITHUB_PRS).demoted is False
    assert panel_status(manifest, JIRA_ISSUES).demoted is True


def test_never_synced_reads_as_failed_with_a_first_run_reason(tmp_path: Path):
    manifest = ManifestStore(tmp_path).load()
    status = panel_status(manifest, BOARD)
    assert status.state == "failed"
    assert "never" in status.reason.lower()


def test_stale_status_compares_the_digest(tmp_path: Path):
    manifest = manifest_with(tmp_path, {"issues": ("jira", "ok", 3, None)})
    assert stale_status(manifest.digest(), manifest, "2026-08-19T09:40:00Z").state == "ok"
    stale = stale_status("sha256:different", manifest, "2026-08-19T09:40:00Z")
    assert stale.state == "stale"
    assert stale.badge == "STALE"
    assert "2026-08-19T09:40:00Z" in stale.reason
    assert "changed since" in stale.reason


def test_data_status_is_hashable_and_frozen():
    import dataclasses

    status = DataStatus(state="ok", badge=None, reason=None)

    # Hashable: usable as a set member / dict key without raising.
    seen = {status}
    assert status in seen

    # Frozen: attribute assignment raises FrozenInstanceError.
    try:
        status.state = "partial"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("expected FrozenInstanceError on attribute assignment")
