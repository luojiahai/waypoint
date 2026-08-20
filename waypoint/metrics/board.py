"""Board state: column WIP against limits, and what is quietly going stale.

On a board with no deadline, age is the only thing that degrades on its own
(§12), so item age and stall time are kept distinct: an item can move between
columns daily and still be old (§10).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from waypoint import clock
from waypoint.metrics import charts
from waypoint.store.derive import IN_PROGRESS, days_between

UNMAPPED = "Unmapped"


@dataclass(frozen=True)
class ColumnWip:
    name: str
    position: int
    count: int
    limit: int | None
    over: bool
    no_limit: bool
    status_ids: tuple[str, ...]
    bar: charts.WipBar


@dataclass(frozen=True)
class InFlightItem:
    key: str
    summary: str
    status: str
    column: str
    assignee_id: str
    assignee_name: str
    age_days: float
    age_text: str
    url: str
    stalled_days: float


@dataclass(frozen=True)
class BoardStrip:
    columns: list[ColumnWip]
    oldest_line: str | None
    any_limits: bool
    empty_message: str | None


@dataclass(frozen=True)
class AgingSection:
    chart: charts.AgingChart
    past_threshold: list[InFlightItem]
    threshold_days: int
    empty_message: str | None


def _column_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM board_columns ORDER BY position").fetchall()


def _status_to_column(con: sqlite3.Connection) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in _column_rows(con):
        for status_id in json.loads(row["status_ids"] or "[]"):
            mapping[str(status_id)] = row["name"]
    return mapping


def columns(con: sqlite3.Connection) -> list[ColumnWip]:
    """Count of issues whose current status maps into each column (§10)."""
    counts: dict[str, int] = {}
    for row in con.execute("SELECT status_id, COUNT(*) AS n FROM jira_issues GROUP BY status_id"):
        counts[str(row["status_id"])] = row["n"]

    built: list[ColumnWip] = []
    for row in _column_rows(con):
        status_ids = tuple(str(s) for s in json.loads(row["status_ids"] or "[]"))
        count = sum(counts.get(status_id, 0) for status_id in status_ids)
        limit = row["wip_limit"] or None
        built.append(
            ColumnWip(
                name=row["name"],
                position=row["position"],
                count=count,
                limit=limit,
                over=bool(limit) and count > limit,
                no_limit=limit is None,
                status_ids=status_ids,
                bar=charts.wip_bar(row["name"], count, limit),
            )
        )
    return built


def in_flight(con: sqlite3.Connection, *, now: datetime) -> list[InFlightItem]:
    now_iso = clock.iso(now)
    mapping = _status_to_column(con)
    items: list[InFlightItem] = []
    rows = con.execute(
        "SELECT i.key, i.summary, i.status, i.status_id, i.url, i.assignee_person_id, "
        "       p.name AS assignee_name, f.first_in_progress_at, f.last_transition_at "
        "FROM jira_issues i "
        "LEFT JOIN people p ON p.id = i.assignee_person_id "
        "LEFT JOIN issue_flow f ON f.issue_key = i.key "
        "WHERE i.status_category = ?",
        (IN_PROGRESS,),
    ).fetchall()
    for row in rows:
        started = row["first_in_progress_at"]
        age = days_between(started, now_iso) or 0.0
        stalled = days_between(row["last_transition_at"], now_iso) or 0.0
        items.append(
            InFlightItem(
                key=row["key"],
                summary=row["summary"] or "",
                status=row["status"] or "",
                column=mapping.get(str(row["status_id"]), UNMAPPED),
                assignee_id=row["assignee_person_id"],
                assignee_name=row["assignee_name"] or "Unassigned",
                age_days=round(age, 1),
                age_text=f"{age:.0f}d",
                url=row["url"] or "",
                stalled_days=round(stalled, 1),
            )
        )
    return sorted(items, key=lambda item: (-item.age_days, item.key))


def board_strip(con: sqlite3.Connection, *, now: datetime) -> BoardStrip:
    cols = columns(con)
    items = in_flight(con, now=now)
    oldest = items[0] if items else None
    return BoardStrip(
        columns=cols,
        oldest_line=(
            None if oldest is None
            else f"Oldest in flight: {oldest.key} · {oldest.column} · {oldest.age_text}"
        ),
        any_limits=any(column.limit for column in cols),
        empty_message=None if items else "Nothing in progress.",
    )


def aging_section(con: sqlite3.Connection, *, now: datetime, threshold_days: int) -> AgingSection:
    cols = columns(con)
    items = in_flight(con, now=now)
    by_column: dict[str, list[InFlightItem]] = {column.name: [] for column in cols}
    by_column.setdefault(UNMAPPED, [])
    for item in items:
        by_column.setdefault(item.column, []).append(item)

    lanes = [
        charts.AgingLaneInput(
            label=column.name,
            sublabel=column.bar.label,
            items=[
                charts.AgingItem(key=item.key, age_days=item.age_days)
                for item in by_column.get(column.name, [])
            ],
        )
        for column in cols
    ]
    unmapped = by_column.get(UNMAPPED, [])
    if unmapped:
        lanes.append(
            charts.AgingLaneInput(
                label=UNMAPPED,
                sublabel=f"{len(unmapped)} · no column",
                items=[charts.AgingItem(item.key, item.age_days) for item in unmapped],
            )
        )
    return AgingSection(
        chart=charts.aging_chart(lanes, threshold_days),
        past_threshold=[item for item in items if item.age_days >= threshold_days],
        threshold_days=threshold_days,
        empty_message=None if items else "Nothing in progress.",
    )
