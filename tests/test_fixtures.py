from pathlib import Path

from waypoint.fixtures import capture, redact
from waypoint.sources.base import RawRecord
from waypoint.store.raw import RawStore


def test_capture_writes_a_jsonl_fixture_with_no_token_anywhere_in_the_bytes(project_dir: Path):
    """`redact` alone proves nothing if `capture` forgets to call it on some
    path. Seed a raw record with a token-shaped key at the top level and
    another nested inside a list of dicts, run the real `capture()` pipeline
    (config load, RawStore.read, redact, file write), then assert on the
    written file's actual bytes on disk -- not on `capture()`'s return value.
    """
    store = RawStore(project_dir / ".waypoint")
    store.write(
        [
            RawRecord(
                source="github",
                entity="pull_requests",
                id="platform/api#1",
                fetched_at="2026-08-19T09:00:00Z",
                payload={
                    "access_token": "SECRET-TOP-LEVEL",
                    "author": {"login": "arivera"},
                    "reviewers": [
                        {"login": "bchen", "Authorization": "Bearer SECRET-NESTED"},
                    ],
                },
            )
        ],
        run_id="run-1",
    )

    out_dir = project_dir / "captured"
    capture(project_dir, out_dir)

    written = (out_dir / "github" / "pull_requests.jsonl").read_bytes().decode()
    assert "SECRET-TOP-LEVEL" not in written
    assert "SECRET-NESTED" not in written
    assert "access_token" not in written
    assert "Authorization" not in written


def test_redact_removes_anything_token_shaped():
    payload = {"access_token": "abc", "nested": {"Authorization": "Bearer x", "keep": 1}}
    cleaned = redact(payload, names={})
    assert "access_token" not in cleaned
    assert "Authorization" not in cleaned["nested"]
    assert cleaned["nested"]["keep"] == 1


def test_redact_replaces_identities_everywhere_they_appear():
    payload = {"author": {"login": "realperson"}, "body": "cc @realperson", "list": ["realperson"]}
    cleaned = redact(payload, names={"realperson": "arivera"})
    assert cleaned["author"]["login"] == "arivera"
    assert cleaned["body"] == "cc @arivera"
    assert cleaned["list"] == ["arivera"]


def test_redact_masks_email_addresses():
    cleaned = redact({"email": "someone@corp.example.com"}, names={})
    assert cleaned["email"] == "person@example.com"


def test_redact_leaves_timestamps_and_counts_alone():
    payload = {"createdAt": "2026-08-14T10:00:00Z", "additions": 220}
    assert redact(payload, names={}) == payload
