import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.store.reports import ReportStore, SidecarError, validate_sidecar

AT = datetime(2026, 8, 19, 9, 40, 0, tzinfo=UTC)

VALID = {
    "skill": "waypoint:delivery-risk",
    "generated_at": "2026-08-19T09:40:00Z",
    "window": {"from": "2026-08-05", "to": "2026-08-19"},
    "inputs_digest": "sha256:abc",
    "items": [
        {
            "severity": "high",
            "title": "Checkout rework has no reviewer coverage",
            "body": "Only Alex has touched it.",
            "evidence": [{"type": "pull_request", "ref": "PR #482", "url": "https://ghe/pr/482"}],
            "question": "Who can back up Alex on the checkout rework this week?",
        }
    ],
}


def test_a_valid_sidecar_passes():
    validate_sidecar(VALID)


def test_a_missing_top_level_key_names_it():
    data = {key: value for key, value in VALID.items() if key != "inputs_digest"}
    with pytest.raises(SidecarError) as exc:
        validate_sidecar(data)
    assert "inputs_digest" in exc.value.message


def test_an_unknown_severity_is_rejected():
    data = json.loads(json.dumps(VALID))
    data["items"][0]["severity"] = "catastrophic"
    with pytest.raises(SidecarError) as exc:
        validate_sidecar(data)
    assert "severity" in exc.value.message
    assert "items/0" in exc.value.path


def test_an_item_missing_evidence_is_rejected_by_the_validator():
    data = json.loads(json.dumps(VALID))
    del data["items"][0]["evidence"]
    with pytest.raises(SidecarError):
        validate_sidecar(data)


def test_evidence_entries_need_a_type_and_a_ref():
    data = json.loads(json.dumps(VALID))
    data["items"][0]["evidence"] = [{"url": "https://ghe/pr/482"}]
    with pytest.raises(SidecarError):
        validate_sidecar(data)


def test_items_must_be_a_list():
    data = json.loads(json.dumps(VALID))
    data["items"] = {"a": 1}
    with pytest.raises(SidecarError):
        validate_sidecar(data)


def test_write_then_latest_round_trips(tmp_path: Path):
    store = ReportStore(tmp_path)
    store.write("waypoint:delivery-risk", VALID, "# Delivery risk\n", at=AT)
    report = store.latest("waypoint:delivery-risk")
    assert report.skill == "waypoint:delivery-risk"
    assert report.items[0].title == VALID["items"][0]["title"]
    assert report.markdown_path.name == "2026-08-19-delivery-risk.md"
    assert report.sidecar_path.name == "2026-08-19-delivery-risk.json"


def test_an_item_with_empty_evidence_is_dropped_on_load(tmp_path: Path):
    store = ReportStore(tmp_path)
    data = json.loads(json.dumps(VALID))
    data["items"].append({
        "severity": "med", "title": "Vibes", "body": "", "evidence": [], "question": None
    })
    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "reports" / "2026-08-19-delivery-risk.json").write_text(json.dumps(data))
    (tmp_path / "reports" / "2026-08-19-delivery-risk.md").write_text("# x")
    report = store.latest("waypoint:delivery-risk")
    assert [item.title for item in report.items] == [VALID["items"][0]["title"]]


def test_a_malformed_sidecar_is_retained_and_linked_as_markdown(tmp_path: Path):
    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "reports" / "2026-08-19-delivery-risk.json").write_text("{not json")
    (tmp_path / "reports" / "2026-08-19-delivery-risk.md").write_text("# Still useful")
    report = ReportStore(tmp_path).latest("waypoint:delivery-risk")
    assert report.malformed is True
    assert report.markdown_path.exists()
    assert report.error


def test_a_non_utf8_sidecar_is_malformed_not_a_crash(tmp_path: Path):
    store = ReportStore(tmp_path)
    store.write("waypoint:delivery-risk", VALID, "# fine", at=datetime(2026, 8, 1, tzinfo=UTC))
    (tmp_path / "reports" / "2026-08-19-growth-review.json").write_bytes(b"\xff\xfe\x00not utf-8")
    (tmp_path / "reports" / "2026-08-19-growth-review.md").write_text("# Still useful")

    reports = ReportStore(tmp_path).all_reports()
    by_name = {report.sidecar_path.name: report for report in reports}

    corrupt = by_name["2026-08-19-growth-review.json"]
    assert corrupt.malformed is True
    assert corrupt.error

    good = by_name["2026-08-01-delivery-risk.json"]
    assert good.malformed is False
    assert good.items[0].title == VALID["items"][0]["title"]


def test_latest_returns_the_newest_report_for_a_skill(tmp_path: Path):
    store = ReportStore(tmp_path)
    store.write("waypoint:delivery-risk", VALID, "# old", at=datetime(2026, 8, 1, tzinfo=UTC))
    store.write("waypoint:delivery-risk", VALID, "# new", at=AT)
    assert ReportStore(tmp_path).latest("waypoint:delivery-risk").markdown_path.read_text() == "# new"


def test_latest_for_an_unrun_skill_is_none(tmp_path: Path):
    assert ReportStore(tmp_path).latest("waypoint:growth-review") is None


def test_person_scoped_reports_do_not_overwrite_each_other(tmp_path: Path):
    store = ReportStore(tmp_path)
    alex = json.loads(json.dumps(VALID))
    alex["skill"] = "waypoint:one-on-one-prep"
    alex["items"][0]["title"] = "Ask Alex about the checkout rework"
    bo = json.loads(json.dumps(alex))
    bo["items"][0]["title"] = "Ask Bo about the review backlog"
    store.write("waypoint:one-on-one-prep", alex, "# alex", at=AT, person_id="alex-rivera")
    store.write("waypoint:one-on-one-prep", bo, "# bo", at=AT, person_id="bo-chen")

    reread = ReportStore(tmp_path)
    assert reread.latest("waypoint:one-on-one-prep", person_id="alex-rivera").items[0].title == (
        "Ask Alex about the checkout rework"
    )
    assert reread.latest("waypoint:one-on-one-prep", person_id="bo-chen").items[0].title == (
        "Ask Bo about the review backlog"
    )
    assert reread.latest("waypoint:one-on-one-prep") is None


def test_a_malformed_person_scoped_sidecar_does_not_fabricate_a_skill_id(tmp_path: Path):
    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "reports" / "2026-08-19-one-on-one-prep-alex-rivera.json").write_text("{not json")
    (tmp_path / "reports" / "2026-08-19-one-on-one-prep-alex-rivera.md").write_text("# alex")

    report = ReportStore(tmp_path).latest("waypoint:one-on-one-prep", person_id="alex-rivera")
    assert report.malformed is True
    assert report.skill != "waypoint:one-on-one-prep-alex-rivera"


def test_reports_never_contain_a_token(tmp_path: Path):
    store = ReportStore(tmp_path)
    store.write("waypoint:delivery-risk", VALID, "# Delivery risk\n", at=AT)
    for path in (tmp_path / "reports").iterdir():
        assert "token" not in path.read_text().lower()
