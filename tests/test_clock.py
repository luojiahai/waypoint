from datetime import UTC, datetime

import pytest

from waypoint import clock


def test_iso_renders_utc_with_z_and_no_fraction():
    dt = datetime(2026, 8, 19, 9, 12, 3, 456789, tzinfo=UTC)
    assert clock.iso(dt) == "2026-08-19T09:12:03Z"


def test_parse_accepts_z_suffix():
    assert clock.parse("2026-08-19T09:12:03Z") == datetime(2026, 8, 19, 9, 12, 3, tzinfo=UTC)


def test_parse_accepts_offset_and_fractional_seconds():
    assert clock.parse("2026-08-19T11:12:03.250+02:00") == datetime(
        2026, 8, 19, 9, 12, 3, 250000, tzinfo=UTC
    )


def test_parse_accepts_naive_and_assumes_utc():
    assert clock.parse("2026-08-19T09:12:03") == datetime(2026, 8, 19, 9, 12, 3, tzinfo=UTC)


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        clock.parse("yesterday")


def test_run_id_is_filesystem_safe():
    dt = datetime(2026, 8, 19, 9, 12, 3, tzinfo=UTC)
    assert clock.run_id(dt) == "2026-08-19T09-12-03Z"


def test_now_is_timezone_aware_utc():
    assert clock.now().tzinfo is UTC
