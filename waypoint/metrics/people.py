"""Per-person signals: load and shape, never output.

Waypoint does not score individuals (§4). This module returns cards and prose,
never a row per person with aligned numeric fields — column alignment IS
side-by-side comparison, and a column is a ranking whether or not it can be
reordered. No function here takes a sort key.

Work mix is prose with the issue keys named, because a chart of two windows makes
the direction of change the loudest thing on the page and a descending line reads
as decline regardless of the label above it (§12).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from waypoint import clock
from waypoint.config import Thresholds, WorkMix
from waypoint.roster import Person, Roster
from waypoint.store.derive import IN_PROGRESS, days_between

BUCKET_COLORS = {"feature": "ok", "bug": "high", "toil": "med", "other": "text-2"}


@dataclass(frozen=True)
class CardLine:
    text: str
    emphasis: str | None = None


@dataclass(frozen=True)
class RosterCard:
    person_id: str
    name: str
    handle: str
    lines: list[CardLine]
    flagged: bool


@dataclass(frozen=True)
class PersonItem:
    ref: str
    title: str
    meta: str
    age_text: str
    url: str


@dataclass(frozen=True)
class PersonPanel:
    label: str
    items: list[PersonItem]
    empty_message: str | None


@dataclass(frozen=True)
class MixBucket:
    name: str
    color: str
    count: int
    keys: list[str]


@dataclass(frozen=True)
class WorkMixView:
    prose: str
    buckets: list[MixBucket]


@dataclass(frozen=True)
class PersonView:
    person_id: str
    name: str
    github_login: str
    jira_account_id: str
    window_from: str
    window_to: str
    window_label: str
    shipped: PersonPanel
    in_flight: PersonPanel
    waiting_on_others: PersonPanel
    others_waiting_on_them: PersonPanel
    work_mix: WorkMixView


def _plural(count: int, singular: str) -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {singular}s"


def workstreams_touched(
    con: sqlite3.Connection, person_id: str, *, since: datetime, until: datetime
) -> int:
    """Distinct epics plus distinct repositories with activity by this person (§10)."""
    low, high = clock.iso(since), clock.iso(until)
    epics = con.execute(
        "SELECT COUNT(DISTINCT parent_key) AS n FROM jira_issues "
        "WHERE assignee_person_id = ? AND parent_key IS NOT NULL "
        "AND ((resolved_at IS NOT NULL AND resolved_at >= ? AND resolved_at < ?) "
        "     OR (updated_at >= ? AND updated_at < ? AND status_category = ?))",
        (person_id, low, high, low, high, IN_PROGRESS),
    ).fetchone()["n"]
    repos = con.execute(
        "SELECT COUNT(DISTINCT repo_id) AS n FROM pull_requests "
        "WHERE author_person_id = ? AND COALESCE(updated_at, created_at) >= ? "
        "AND COALESCE(updated_at, created_at) < ?",
        (person_id, low, high),
    ).fetchone()["n"]
    return epics + repos


def _last_activity(con: sqlite3.Connection, person_id: str) -> str | None:
    row = con.execute(
        "SELECT MAX(stamp) AS stamp FROM ("
        "  SELECT MAX(COALESCE(updated_at, created_at)) AS stamp FROM pull_requests "
        "  WHERE author_person_id = ?"
        "  UNION ALL SELECT MAX(submitted_at) FROM pr_reviews WHERE reviewer_person_id = ?"
        "  UNION ALL SELECT MAX(updated_at) FROM jira_issues WHERE assignee_person_id = ?"
        ")",
        (person_id, person_id, person_id),
    ).fetchone()
    return row["stamp"]


def roster_cards(
    con: sqlite3.Connection, roster: Roster, *, now: datetime, thresholds: Thresholds
) -> list[RosterCard]:
    now_iso = clock.iso(now)
    cards: list[RosterCard] = []
    for person in roster.active_people():
        open_prs = con.execute(
            "SELECT COUNT(*) AS n FROM pull_requests WHERE author_person_id = ? AND state = 'OPEN'",
            (person.id,),
        ).fetchone()["n"]
        awaiting = con.execute(
            "SELECT COUNT(*) AS n FROM pr_review_requests r "
            "JOIN pull_requests p ON p.id = r.pr_id "
            "LEFT JOIN pr_reviews v ON v.pr_id = r.pr_id AND v.reviewer_person_id = r.requested_person_id "
            "WHERE r.requested_person_id = ? AND p.state = 'OPEN' AND v.id IS NULL",
            (person.id,),
        ).fetchone()["n"]
        oldest_wait = con.execute(
            "SELECT MAX(f.review_wait_current) AS h FROM pr_review_requests r "
            "JOIN pr_flow f ON f.pr_id = r.pr_id WHERE r.requested_person_id = ?",
            (person.id,),
        ).fetchone()["h"] or 0.0
        in_flight_count = con.execute(
            "SELECT COUNT(*) AS n FROM jira_issues WHERE assignee_person_id = ? "
            "AND status_category = ?",
            (person.id, IN_PROGRESS),
        ).fetchone()["n"]
        oldest_age = con.execute(
            "SELECT MIN(f.first_in_progress_at) AS stamp FROM jira_issues i "
            "JOIN issue_flow f ON f.issue_key = i.key "
            "WHERE i.assignee_person_id = ? AND i.status_category = ?",
            (person.id, IN_PROGRESS),
        ).fetchone()["stamp"]
        age_days = days_between(oldest_age, now_iso) or 0.0
        streams = workstreams_touched(con, person.id, since=clock.parse("1970-01-01T00:00:00Z"),
                                      until=now)
        last = _last_activity(con, person.id)
        idle_days = days_between(last, now_iso)

        wait_crossed = oldest_wait / 24 >= thresholds.pr_review_wait_days
        age_crossed = age_days >= thresholds.issue_aging_days

        lines = [
            CardLine(
                f"{_plural(open_prs, 'open PR')} · {_plural(in_flight_count, 'issue')} in flight",
            ),
            CardLine(
                f"{_plural(awaiting, 'PR')} awaiting their review"
                + (f" · oldest {oldest_wait / 24:.0f}d" if awaiting else ""),
                emphasis="med" if wait_crossed and awaiting else None,
            ),
            CardLine(
                f"{_plural(streams, 'workstream')} touched · "
                + ("no activity recorded" if idle_days is None
                   else f"last active {idle_days:.0f}d ago")
                + (f" · oldest item {age_days:.0f}d" if in_flight_count else ""),
                emphasis="med" if age_crossed and in_flight_count else None,
            ),
        ]
        cards.append(
            RosterCard(
                person_id=person.id,
                name=person.name,
                handle=" · ".join(filter(None, (person.github_login, person.jira_account_id))),
                lines=lines,
                flagged=any(line.emphasis for line in lines),
            )
        )
    return cards


def _panel(label: str, items: list[PersonItem], empty: str) -> PersonPanel:
    return PersonPanel(label=label, items=items, empty_message=None if items else empty)


def person_view(
    con: sqlite3.Connection, person: Person, *, now: datetime, since: datetime, work_mix: WorkMix
) -> PersonView:
    low, high = clock.iso(since), clock.iso(now)
    span_days = max(1, round((now - since).total_seconds() / 86400))
    prior_low = clock.iso(since - (now - since))

    shipped_issues = con.execute(
        "SELECT key, summary, resolved_at, url FROM jira_issues "
        "WHERE assignee_person_id = ? AND resolved_at >= ? AND resolved_at < ? ORDER BY key",
        (person.id, low, high),
    ).fetchall()
    shipped_prs = con.execute(
        "SELECT id, title, merged_at, url FROM pull_requests "
        "WHERE author_person_id = ? AND merged_at >= ? AND merged_at < ? ORDER BY id",
        (person.id, low, high),
    ).fetchall()
    shipped = [
        PersonItem(row["key"], row["summary"] or "", "resolved", row["resolved_at"][:10], row["url"] or "")
        for row in shipped_issues
    ] + [
        PersonItem(row["id"], row["title"] or "", "merged", row["merged_at"][:10], row["url"] or "")
        for row in shipped_prs
    ]

    in_flight_rows = con.execute(
        "SELECT i.key, i.summary, i.status, i.url, f.first_in_progress_at FROM jira_issues i "
        "LEFT JOIN issue_flow f ON f.issue_key = i.key "
        "WHERE i.assignee_person_id = ? AND i.status_category = ? "
        "ORDER BY f.first_in_progress_at",
        (person.id, IN_PROGRESS),
    ).fetchall()
    in_flight_items = [
        PersonItem(
            row["key"], row["summary"] or "", row["status"] or "",
            f"{days_between(row['first_in_progress_at'], high) or 0:.0f}d", row["url"] or "",
        )
        for row in in_flight_rows
    ]

    waiting_rows = con.execute(
        "SELECT p.id, p.title, p.url, f.review_wait_current FROM pull_requests p "
        "JOIN pr_flow f ON f.pr_id = p.id "
        "WHERE p.author_person_id = ? AND p.state = 'OPEN' "
        "AND f.review_wait_current IS NOT NULL ORDER BY f.review_wait_current DESC",
        (person.id,),
    ).fetchall()
    waiting_on_others = [
        PersonItem(row["id"], row["title"] or "", "awaiting review",
                   f"{row['review_wait_current'] / 24:.0f}d", row["url"] or "")
        for row in waiting_rows
    ]

    owed_rows = con.execute(
        "SELECT p.id, p.title, p.url, f.review_wait_current FROM pr_review_requests r "
        "JOIN pull_requests p ON p.id = r.pr_id "
        "LEFT JOIN pr_flow f ON f.pr_id = p.id "
        "LEFT JOIN pr_reviews v ON v.pr_id = p.id AND v.reviewer_person_id = r.requested_person_id "
        "WHERE r.requested_person_id = ? AND p.state = 'OPEN' AND v.id IS NULL ORDER BY p.id",
        (person.id,),
    ).fetchall()
    owed = [
        PersonItem(row["id"], row["title"] or "", "review requested",
                   f"{(row['review_wait_current'] or 0) / 24:.0f}d", row["url"] or "")
        for row in owed_rows
    ]

    def bucket_window(start: str, end: str) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {}
        rows = con.execute(
            "SELECT key, type FROM jira_issues WHERE assignee_person_id = ? "
            "AND resolved_at >= ? AND resolved_at < ? ORDER BY key",
            (person.id, start, end),
        ).fetchall()
        for row in rows:
            buckets.setdefault(work_mix.bucket_for(row["type"] or ""), []).append(row["key"])
        return buckets

    current = bucket_window(low, high)
    prior = bucket_window(prior_low, low)
    current_total = sum(len(keys) for keys in current.values())
    prior_total = sum(len(keys) for keys in prior.values())
    if current_total == prior_total:
        change = "Volume is unchanged between the two windows."
    elif current_total > prior_total:
        change = "Volume is higher than the prior window."
    else:
        change = "Volume is lower than the prior window."
    prose = (
        f"{current_total} resolved in this window of {span_days}d, "
        f"{prior_total} in the prior window of equal length. {change}"
    )
    buckets = [
        MixBucket(name=name, color=BUCKET_COLORS[name], count=len(current.get(name, [])),
                  keys=current.get(name, []))
        for name in ("feature", "bug", "toil")
    ]
    if current.get("other"):
        buckets.append(
            MixBucket(name="other", color=BUCKET_COLORS["other"],
                      count=len(current["other"]), keys=current["other"])
        )

    return PersonView(
        person_id=person.id,
        name=person.name,
        github_login=person.github_login,
        jira_account_id=person.jira_account_id,
        window_from=low,
        window_to=high,
        window_label=f"since {low[:10]} · {span_days}d",
        shipped=_panel("Shipped", shipped, "Nothing shipped in this window."),
        in_flight=_panel("In flight", in_flight_items, "Nothing in flight."),
        waiting_on_others=_panel("Waiting on someone else", waiting_on_others,
                                 "Not waiting on anyone."),
        others_waiting_on_them=_panel("Someone else waiting on them", owed,
                                      "No one is waiting on them."),
        work_mix=WorkMixView(prose=prose, buckets=buckets),
    )
