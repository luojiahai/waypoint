from datetime import UTC, datetime
from pathlib import Path

from waypoint.store.views import PersonViews


def test_unseen_person_has_no_last_viewed(tmp_path: Path):
    assert PersonViews(tmp_path).last_viewed("alex-rivera") is None


def test_record_then_read_round_trips(tmp_path: Path):
    views = PersonViews(tmp_path)
    views.record("alex-rivera", datetime(2026, 8, 12, 9, 0, tzinfo=UTC))
    assert PersonViews(tmp_path).last_viewed("alex-rivera") == datetime(
        2026, 8, 12, 9, 0, tzinfo=UTC
    )


def test_recording_one_person_leaves_the_others_alone(tmp_path: Path):
    views = PersonViews(tmp_path)
    views.record("alex-rivera", datetime(2026, 8, 12, 9, 0, tzinfo=UTC))
    views.record("bo-chen", datetime(2026, 8, 13, 9, 0, tzinfo=UTC))
    assert PersonViews(tmp_path).last_viewed("alex-rivera").day == 12
