from pathlib import Path

from waypoint.metrics.status import (
    BOARD, GITHUB_PRS, JIRA_ISSUES, DataStatus, OK_STATUS, panel_status, stale_status,
    sync_label, worst_of
)
from waypoint import clock
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


def test_failed_reason_reports_known_failure_alongside_missing_entity(tmp_path: Path):
    manifest = manifest_with(tmp_path, {
        "issues": ("jira", "failed", 0, "401 authentication failed"),
    })
    status = panel_status(manifest, JIRA_ISSUES)
    assert status.badge == "FAILED"
    assert "jira/changelogs" in status.reason
    assert "never synced" in status.reason.lower()
    assert "401 authentication failed" in status.reason


def test_unaffected_clause_omitted_for_a_mixed_source_group(tmp_path: Path):
    from waypoint.metrics.status import EVERYTHING

    manifest = manifest_with(tmp_path, {
        "pull_requests": ("github", "ok", 12, None),
        "reviews": ("github", "ok", 5, None),
        "review_requests": ("github", "ok", 2, None),
        "issues": ("jira", "failed", 0, "timeout"),
        "changelogs": ("jira", "ok", 3, None),
        "board_config": ("jira", "ok", 1, None),
    })
    status = panel_status(manifest, EVERYTHING)
    assert status.badge == "FAILED"
    assert "unaffected" not in status.reason.lower()


def test_unaffected_clause_still_present_for_a_single_source_group(tmp_path: Path):
    manifest = manifest_with(tmp_path, {
        "issues": ("jira", "failed", 0, "401 authentication failed"),
        "changelogs": ("jira", "failed", 0, "401 authentication failed"),
    })
    status = panel_status(manifest, JIRA_ISSUES)
    assert "GitHub panels are unaffected." in status.reason


def test_worst_of_keeps_the_most_severe_status(tmp_path: Path):
    failed = DataStatus(state="failed", badge="FAILED", reason="jira failed")
    partial = DataStatus(state="partial", badge="PARTIAL", reason="rate limited")
    stale = DataStatus(state="stale", badge="STALE", reason="data changed since")

    assert worst_of(failed, stale) is failed
    assert worst_of(stale, failed) is failed
    assert worst_of(partial, stale) is partial
    assert worst_of(OK_STATUS, stale) is stale
    assert worst_of(OK_STATUS, None) is OK_STATUS
    assert worst_of() is OK_STATUS


def test_worst_of_never_lets_a_fresh_report_erase_a_data_failure(tmp_path: Path):
    """The bug this helper exists for: `stale_status` returns a *truthy* OK
    dataclass when a report is fresh, so any "pick the report status if there
    is one" rule would drop the FAILED demotion beside it."""
    manifest = manifest_with(tmp_path, {"issues": ("jira", "failed", 0, "401 from Jira")})
    data = panel_status(manifest, JIRA_ISSUES)
    fresh_report = stale_status(manifest.digest(), manifest, "2026-08-19T09:40:00Z")

    assert fresh_report is OK_STATUS  # truthy, and the reason the bug existed
    assert worst_of(data, fresh_report).badge == "FAILED"


def test_sync_label_reads_never_synced_before_any_run(tmp_path: Path):
    label = sync_label(ManifestStore(tmp_path).load(), clock.parse("2026-08-19T12:00:00Z"))
    assert label.text == "never synced"
    assert label.state == "ok"


def test_sync_label_states_carry_the_severity_that_colours_them(tmp_path: Path):
    now = clock.parse("2026-08-19T12:00:00Z")
    partial = manifest_with(tmp_path, {"issues": ("jira", "partial", 2, "rate limited")})
    assert sync_label(partial, now).state == "partial"
    assert "last sync partial" in sync_label(partial, now).text

    failed = manifest_with(tmp_path, {"issues": ("jira", "failed", 0, "401")})
    assert sync_label(failed, now).state == "failed"
    assert "last sync failed" in sync_label(failed, now).text


def test_sync_label_counts_elapsed_time_in_hours_then_minutes(tmp_path: Path):
    manifest = manifest_with(tmp_path, {"issues": ("jira", "ok", 3, None)})
    # manifest_with stamps every run at 2026-08-19T09:12:03Z.
    assert sync_label(manifest, clock.parse("2026-08-19T12:12:03Z")).text == (
        "synced 09:12 · 3h ago"
    )
    assert sync_label(manifest, clock.parse("2026-08-19T09:42:03Z")).text == (
        "synced 09:12 · 30m ago"
    )
