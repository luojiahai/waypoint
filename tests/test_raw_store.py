import json
from pathlib import Path

from waypoint.sources.base import RawRecord
from waypoint.store.raw import RawStore


def rec(entity: str, rid: str, fetched: str, **payload) -> RawRecord:
    return RawRecord(
        source="github", entity=entity, id=rid, fetched_at=fetched, payload=payload
    )


def test_write_routes_records_into_per_entity_snapshot_files(tmp_path: Path):
    store = RawStore(tmp_path)
    counts = store.write(
        [
            rec("pull_requests", "platform/api#1", "2026-08-19T09:12:03Z", title="one"),
            rec("pull_requests", "platform/api#2", "2026-08-19T09:12:03Z", title="two"),
            rec("reviews", "r1", "2026-08-19T09:12:03Z", state="APPROVED"),
        ],
        run_id="2026-08-19T09-12-03Z",
    )
    assert counts == {"github/pull_requests": 2, "github/reviews": 1}
    path = tmp_path / "raw" / "github" / "pull_requests" / "2026-08-19T09-12-03Z.jsonl"
    assert path.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["payload"]["title"] == "one"


def test_envelope_has_exactly_the_specified_keys(tmp_path: Path):
    store = RawStore(tmp_path)
    store.write([rec("pull_requests", "x#1", "2026-08-19T09:12:03Z", a=1)], "run-1")
    line = (tmp_path / "raw" / "github" / "pull_requests" / "run-1.jsonl").read_text().strip()
    assert set(json.loads(line)) == {"source", "entity", "id", "fetched_at", "payload"}


def test_no_file_is_created_for_an_entity_with_no_records(tmp_path: Path):
    store = RawStore(tmp_path)
    store.write([], "run-1")
    assert not (tmp_path / "raw" / "github").exists()


def test_read_returns_snapshots_in_chronological_order(tmp_path: Path):
    store = RawStore(tmp_path)
    store.write([rec("pull_requests", "x#1", "2026-08-18T09:00:00Z", v=1)], "2026-08-18T09-00-00Z")
    store.write([rec("pull_requests", "x#1", "2026-08-19T09:00:00Z", v=2)], "2026-08-19T09-00-00Z")
    got = list(store.read("github", "pull_requests"))
    assert [r.payload["v"] for r in got] == [1, 2]


def test_write_never_mutates_an_existing_snapshot(tmp_path: Path):
    store = RawStore(tmp_path)
    store.write([rec("pull_requests", "x#1", "2026-08-18T09:00:00Z", v=1)], "run-1")
    first = (tmp_path / "raw" / "github" / "pull_requests" / "run-1.jsonl").read_text()
    store.write([rec("pull_requests", "x#1", "2026-08-19T09:00:00Z", v=2)], "run-2")
    assert (tmp_path / "raw" / "github" / "pull_requests" / "run-1.jsonl").read_text() == first


def test_entities_lists_every_source_entity_pair_on_disk(tmp_path: Path):
    store = RawStore(tmp_path)
    store.write(
        [
            rec("pull_requests", "x#1", "2026-08-19T09:00:00Z"),
            RawRecord("jira", "issues", "PROJ-1", "2026-08-19T09:00:00Z", {}),
        ],
        "run-1",
    )
    assert store.entities() == [("github", "pull_requests"), ("jira", "issues")]


def test_read_of_an_unknown_entity_is_empty_not_an_error(tmp_path: Path):
    assert list(RawStore(tmp_path).read("github", "nothing")) == []


def test_records_round_trip_through_json(tmp_path: Path):
    original = rec("pull_requests", "x#1", "2026-08-19T09:00:00Z", nested={"a": [1, 2]})
    assert RawRecord.from_json(original.to_json()) == original
