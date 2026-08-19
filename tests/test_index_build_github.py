from datetime import UTC, datetime
from pathlib import Path

from waypoint.config import load_config
from waypoint.sources.base import RawRecord
from waypoint.store.index import build, connect, latest_records
from waypoint.store.raw import RawStore

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def pr_payload(number: int, **overrides) -> dict:
    payload = {
        "number": number,
        "title": f"PROJ-{number} work",
        "url": f"https://ghe.corp.example.com/platform/api/pull/{number}",
        "state": "OPEN",
        "isDraft": False,
        "body": "",
        "createdAt": "2026-08-14T10:00:00Z",
        "updatedAt": "2026-08-18T12:00:00Z",
        "mergedAt": None,
        "closedAt": None,
        "additions": 10,
        "deletions": 2,
        "changedFiles": 3,
        "baseRefName": "main",
        "headRefName": f"proj-{number}",
        "author": {"login": "arivera"},
        "labels": {"nodes": [{"name": "needs-review"}]},
        "commits": {"nodes": [{"commit": {"authoredDate": "2026-08-13T08:00:00Z"}}]},
        "timelineItems": {"nodes": []},
        "reviews": {"nodes": []},
    }
    payload.update(overrides)
    return payload


def write_prs(root: Path, records: list[RawRecord], run_id: str = "run-1") -> None:
    RawStore(root).write(records, run_id)


def test_schema_creates_every_table(project_dir: Path):
    cfg = load_config(project_dir / ".waypoint")
    build(project_dir / ".waypoint", cfg, now=NOW)
    con = connect(project_dir / ".waypoint" / "index.db", read_only=True)
    names = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "people", "repos", "pull_requests", "pr_reviews", "pr_review_requests",
        "jira_issues", "issue_transitions", "board_columns", "issue_pr_links",
        "sync_runs", "pr_flow", "issue_flow", "unattributed", "meta",
    } <= names


def test_people_table_includes_the_unattributed_bucket(project_dir: Path):
    cfg = load_config(project_dir / ".waypoint")
    build(project_dir / ".waypoint", cfg, now=NOW)
    con = connect(project_dir / ".waypoint" / "index.db", read_only=True)
    ids = [r["id"] for r in con.execute("SELECT id FROM people ORDER BY id")]
    assert ids == ["alex-rivera", "bo-chen", "unattributed"]


def test_pull_requests_are_inserted_with_resolved_authors(project_dir: Path):
    root = project_dir / ".waypoint"
    write_prs(root, [RawRecord("github", "pull_requests", "platform/api#482",
                               "2026-08-19T09:00:00Z", pr_payload(482))])
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    row = con.execute("SELECT * FROM pull_requests").fetchone()
    assert row["id"] == "platform/api#482"
    assert row["repo_id"] == "platform/api"
    assert row["number"] == 482
    assert row["author_person_id"] == "alex-rivera"
    assert row["first_commit_at"] == "2026-08-13T08:00:00Z"
    assert row["ready_at"] == "2026-08-14T10:00:00Z"
    assert row["draft"] == 0


def test_ready_at_comes_from_the_ready_for_review_event_when_present(project_dir: Path):
    root = project_dir / ".waypoint"
    payload = pr_payload(
        483,
        isDraft=False,
        timelineItems={"nodes": [{"__typename": "ReadyForReviewEvent",
                                  "createdAt": "2026-08-15T09:00:00Z"}]},
    )
    write_prs(root, [RawRecord("github", "pull_requests", "platform/api#483",
                               "2026-08-19T09:00:00Z", payload)])
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert con.execute("SELECT ready_at FROM pull_requests").fetchone()[0] == "2026-08-15T09:00:00Z"


def test_a_still_draft_pr_has_no_ready_at(project_dir: Path):
    root = project_dir / ".waypoint"
    write_prs(root, [RawRecord("github", "pull_requests", "platform/api#484",
                               "2026-08-19T09:00:00Z", pr_payload(484, isDraft=True))])
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert con.execute("SELECT ready_at FROM pull_requests").fetchone()[0] is None


def test_rest_shaped_payloads_are_read_too(project_dir: Path):
    root = project_dir / ".waypoint"
    rest = {
        "number": 490,
        "title": "REST shaped",
        "html_url": "https://ghe/platform/api/pull/490",
        "state": "closed",
        "draft": False,
        "body": "",
        "created_at": "2026-08-14T10:00:00Z",
        "updated_at": "2026-08-18T12:00:00Z",
        "merged_at": "2026-08-17T10:00:00Z",
        "closed_at": "2026-08-17T10:00:00Z",
        "additions": 1, "deletions": 1, "changed_files": 1,
        "base": {"ref": "main"}, "head": {"ref": "rest"},
        "user": {"login": "bchen"},
        "labels": [{"name": "x"}],
    }
    write_prs(root, [RawRecord("github", "pull_requests", "platform/api#490",
                               "2026-08-19T09:00:00Z", rest)])
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    row = con.execute("SELECT * FROM pull_requests").fetchone()
    assert row["author_person_id"] == "bo-chen"
    assert row["merged_at"] == "2026-08-17T10:00:00Z"
    assert row["url"] == "https://ghe/platform/api/pull/490"


def test_unknown_login_is_attributed_to_unattributed_and_recorded(project_dir: Path):
    root = project_dir / ".waypoint"
    payload = pr_payload(485, author={"login": "stranger"})
    write_prs(root, [RawRecord("github", "pull_requests", "platform/api#485",
                               "2026-08-19T09:00:00Z", payload)])
    result = build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert con.execute("SELECT author_person_id FROM pull_requests").fetchone()[0] == "unattributed"
    assert ("github", "stranger", "author", 1) in result.unattributed


def test_last_writer_wins_across_overlapping_snapshots(project_dir: Path):
    root = project_dir / ".waypoint"
    store = RawStore(root)
    store.write([RawRecord("github", "pull_requests", "platform/api#482",
                           "2026-08-18T09:00:00Z", pr_payload(482, title="old"))], "run-1")
    store.write([RawRecord("github", "pull_requests", "platform/api#482",
                           "2026-08-19T09:00:00Z", pr_payload(482, title="new"))], "run-2")
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    rows = con.execute("SELECT title FROM pull_requests").fetchall()
    assert [r["title"] for r in rows] == ["new"]


def test_latest_records_prefers_the_newest_fetched_at_regardless_of_file_order(project_dir: Path):
    root = project_dir / ".waypoint"
    store = RawStore(root)
    store.write([RawRecord("github", "pull_requests", "x#1", "2026-08-19T09:00:00Z", {"v": 2})], "run-1")
    store.write([RawRecord("github", "pull_requests", "x#1", "2026-08-18T09:00:00Z", {"v": 1})], "run-2")
    assert [r.payload["v"] for r in latest_records(store, "github", "pull_requests")] == [2]


def _table_names(con) -> list[str]:
    return sorted(
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )


def _snapshot(con) -> dict:
    """Deterministic content snapshot: row counts plus full ordered rows per table."""
    snapshot = {}
    for table in _table_names(con):
        n_cols = len(con.execute(f"PRAGMA table_info({table})").fetchall())
        order_by = ", ".join(str(i) for i in range(1, n_cols + 1))
        rows = con.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
        snapshot[table] = (len(rows), [tuple(row) for row in rows])
    return snapshot


def test_build_is_idempotent(project_dir: Path):
    root = project_dir / ".waypoint"
    write_prs(root, [RawRecord("github", "pull_requests", "platform/api#482",
                               "2026-08-19T09:00:00Z", pr_payload(482))])
    cfg = load_config(root)
    build(root, cfg, now=NOW)
    con = connect(root / "index.db", read_only=True)
    first = _snapshot(con)
    con.close()
    build(root, cfg, now=NOW)
    con = connect(root / "index.db", read_only=True)
    second = _snapshot(con)
    con.close()
    assert second == first


def test_a_failed_build_leaves_the_existing_index_untouched(project_dir: Path, monkeypatch):
    root = project_dir / ".waypoint"
    cfg = load_config(root)
    build(root, cfg, now=NOW)
    good = (root / "index.db").read_bytes()

    import waypoint.store.index as index_module

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(index_module, "_load_github", explode)
    try:
        build(root, cfg, now=NOW)
    except Exception:
        pass
    assert (root / "index.db").read_bytes() == good
    assert not (root / "index.db.tmp").exists()


def test_reviews_and_review_requests_are_inserted(project_dir: Path):
    root = project_dir / ".waypoint"
    store = RawStore(root)
    store.write(
        [
            RawRecord("github", "pull_requests", "platform/api#482",
                      "2026-08-19T09:00:00Z", pr_payload(482)),
            RawRecord("github", "reviews", "platform/api#482:review:RV_1", "2026-08-19T09:00:00Z",
                      {"id": "RV_1", "state": "APPROVED", "submittedAt": "2026-08-16T11:00:00Z",
                       "author": {"login": "bchen"}, "pull_request_id": "platform/api#482"}),
            RawRecord("github", "review_requests", "platform/api#482:requested:bchen:2026-08-14T10:05:00Z",
                      "2026-08-19T09:00:00Z",
                      {"pull_request_id": "platform/api#482", "login": "bchen",
                       "requested_at": "2026-08-14T10:05:00Z"}),
        ],
        "run-1",
    )
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    review = con.execute("SELECT * FROM pr_reviews").fetchone()
    assert review["reviewer_person_id"] == "bo-chen"
    assert review["state"] == "APPROVED"
    request = con.execute("SELECT * FROM pr_review_requests").fetchone()
    assert request["requested_person_id"] == "bo-chen"
    assert request["requested_at"] == "2026-08-14T10:05:00Z"


def test_review_request_collision_prefers_real_timestamp_over_unknown(project_dir: Path):
    """Override 4: REST and GraphQL can both emit a request for the same
    (pr_id, requested_login) pair -- REST with the literal "unknown" timestamp,
    GraphQL with the real event time. Whichever transport an instance last used
    should not silently duplicate the row; the real timestamp wins.
    """
    root = project_dir / ".waypoint"
    store = RawStore(root)
    store.write(
        [
            RawRecord("github", "pull_requests", "platform/api#482",
                      "2026-08-19T09:00:00Z", pr_payload(482)),
            RawRecord("github", "review_requests", "platform/api#482:requested:bchen:unknown",
                      "2026-08-19T09:00:00Z",
                      {"pull_request_id": "platform/api#482", "login": "bchen",
                       "requested_at": "unknown"}),
            RawRecord("github", "review_requests",
                      "platform/api#482:requested:bchen:2026-08-14T10:05:00Z",
                      "2026-08-19T09:00:01Z",
                      {"pull_request_id": "platform/api#482", "login": "bchen",
                       "requested_at": "2026-08-14T10:05:00Z"}),
        ],
        "run-1",
    )
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    requests = con.execute("SELECT * FROM pr_review_requests").fetchall()
    assert len(requests) == 1
    assert requests[0]["requested_at"] == "2026-08-14T10:05:00Z"


def test_repos_are_derived_from_config_not_from_data(project_dir: Path):
    root = project_dir / ".waypoint"
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    assert [r["id"] for r in con.execute("SELECT id FROM repos ORDER BY id")] == [
        "platform/api", "platform/web"
    ]


def test_meta_records_the_build_time(project_dir: Path):
    root = project_dir / ".waypoint"
    build(root, load_config(root), now=NOW)
    con = connect(root / "index.db", read_only=True)
    value = con.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()[0]
    assert value == "2026-08-19T12:00:00Z"
