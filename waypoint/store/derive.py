"""Per-item durations precomputed so metric functions stay simple selects.

Everything here is stated once, in one place, because ambiguity in these
definitions produces silently wrong numbers (§10).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from waypoint import clock
from waypoint.config import Config

TODO = "To Do"
IN_PROGRESS = "In Progress"
DONE = "Done"

_CATEGORY_HINTS = {
    "done": DONE,
    "closed": DONE,
    "resolved": DONE,
    "shipped": DONE,
    "to do": TODO,
    "backlog": TODO,
    "open": TODO,
}


def hours_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (clock.parse(end) - clock.parse(start)).total_seconds() / 3600


def days_between(start: str | None, end: str | None) -> float | None:
    hours = hours_between(start, end)
    return None if hours is None else hours / 24


def _category_map(con: sqlite3.Connection) -> dict[str, str]:
    """Status name -> status category, learned from the issues themselves.

    Transitions record only the status name. Every status an issue currently
    occupies carries its category, which covers the statuses in use; anything
    else falls back to a name hint, and unknown names are treated as In Progress
    because a status that is neither backlog nor done is work in flight.
    """
    mapping = {
        row["status"]: row["status_category"]
        for row in con.execute(
            "SELECT DISTINCT status, status_category FROM jira_issues WHERE status IS NOT NULL"
        )
    }
    return mapping


def _category_for(name: str | None, mapping: dict[str, str]) -> str:
    if not name:
        return IN_PROGRESS
    if name in mapping:
        return mapping[name]
    return _CATEGORY_HINTS.get(name.casefold(), IN_PROGRESS)


def _hours_if_ordered(start: str | None, end: str | None) -> float | None:
    """`hours_between`, but None when `end` precedes `start`.

    A later event timestamped before its predecessor (a backdated commit, a
    review submitted before the PR was marked ready) is real GitHub data, not
    a bug in this module. Rendering it as a negative duration would be a
    confidently wrong number on the dashboard, which the spec forbids, so the
    ordering check lives here at the call site rather than inside
    `hours_between` itself — that helper is shared with Task 15's metrics and
    must keep reporting whatever a caller feeds it.
    """
    if start and end and end < start:
        return None
    return hours_between(start, end)


def _derive_pr_flow(con: sqlite3.Connection, cfg: Config, now: datetime) -> int:
    bots = {login.casefold() for login in cfg.github.bot_logins}
    now_iso = clock.iso(now)
    rows = []
    for pr in con.execute("SELECT * FROM pull_requests"):
        reviews = con.execute(
            "SELECT reviewer_person_id, reviewer_login, submitted_at FROM pr_reviews "
            "WHERE pr_id = ? AND submitted_at IS NOT NULL ORDER BY submitted_at",
            (pr["id"],),
        ).fetchall()
        first_review = next(
            (
                review["submitted_at"]
                for review in reviews
                if review["reviewer_person_id"] != pr["author_person_id"]
                and (review["reviewer_login"] or "").casefold() not in bots
            ),
            None,
        )
        start = pr["ready_at"] or pr["created_at"]
        review_wait = None
        if pr["state"] == "OPEN" and first_review is None and start:
            review_wait = _hours_if_ordered(start, now_iso)
        rows.append(
            (
                pr["id"],
                _hours_if_ordered(start, first_review),
                _hours_if_ordered(pr["first_commit_at"] or pr["created_at"], pr["merged_at"]),
                _hours_if_ordered(first_review, pr["merged_at"]),
                review_wait,
            )
        )
    con.execute("DELETE FROM pr_flow")
    con.executemany("INSERT INTO pr_flow VALUES (?,?,?,?,?)", rows)
    return len(rows)


def _derive_issue_flow(con: sqlite3.Connection, now: datetime) -> int:
    mapping = _category_map(con)
    now_iso = clock.iso(now)
    rows = []
    for issue in con.execute("SELECT key FROM jira_issues"):
        key = issue["key"]
        transitions = con.execute(
            "SELECT to_value, changed_at FROM issue_transitions "
            "WHERE issue_key = ? AND field = 'status' ORDER BY changed_at",
            (key,),
        ).fetchall()
        first_in_progress = None
        first_done = None
        last_transition = None
        for transition in transitions:
            category = _category_for(transition["to_value"], mapping)
            last_transition = transition["changed_at"]
            if category == IN_PROGRESS and first_in_progress is None:
                first_in_progress = transition["changed_at"]
            if category == DONE and first_done is None:
                first_done = transition["changed_at"]
        cycle = (
            hours_between(first_in_progress, first_done)
            if first_in_progress and first_done and first_done >= first_in_progress
            else None
        )
        rows.append(
            (
                key, first_in_progress, first_done, cycle, last_transition,
                days_between(last_transition, now_iso),
            )
        )
    con.execute("DELETE FROM issue_flow")
    con.executemany("INSERT INTO issue_flow VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def derive_all(con: sqlite3.Connection, cfg: Config, *, now: datetime) -> dict[str, int]:
    return {
        "pr_flow": _derive_pr_flow(con, cfg, now),
        "issue_flow": _derive_issue_flow(con, now),
    }
