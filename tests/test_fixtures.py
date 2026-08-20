from waypoint.fixtures import redact


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
