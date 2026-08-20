"""Epic completion, activity, and projection drift.

Projection is remaining work divided by the trailing four-week completion rate.
When that rate is zero the panel says "no recent progress" rather than inventing
a date (§10).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from waypoint import clock
from waypoint.config import JiraConfig
from waypoint.metrics import charts

POINTS_COVERAGE = 0.8


@dataclass(frozen=True)
class EpicRow:
    key: str
    name: str
    url: str
    done: float
    total: float
    completion_text: str
    progress: charts.Progress
    projection_text: str
    projection_state: str
    drift_days: int | None
    activity_text: str


@dataclass(frozen=True)
class EpicsSection:
    rows: list[EpicRow]
    basis: str
    basis_label: str
    empty_message: str | None


def _due_date(labels_json: str | None) -> datetime | None:
    for label in json.loads(labels_json or "[]"):
        if label.startswith("due:"):
            try:
                return clock.parse(label[4:] + "T00:00:00Z")
            except ValueError:
                return None
    return None


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def weight(row, basis: str) -> float:
    if basis == "points":
        return float(row["story_points"] or 0)
    return 1.0


def epics(
    con: sqlite3.Connection, *, now: datetime, jira: JiraConfig, trailing_weeks: int = 4
) -> EpicsSection:
    parents = con.execute(
        "SELECT parent_key, COUNT(*) AS n FROM jira_issues "
        "WHERE parent_key IS NOT NULL GROUP BY parent_key ORDER BY parent_key"
    ).fetchall()
    if not parents:
        return EpicsSection(rows=[], basis="count", basis_label="by issue count",
                            empty_message="No epics with child issues.")

    all_children = con.execute(
        "SELECT i.key, i.parent_key, i.status_category, i.story_points, f.first_done_at "
        "FROM jira_issues i LEFT JOIN issue_flow f ON f.issue_key = i.key "
        "WHERE i.parent_key IS NOT NULL"
    ).fetchall()
    with_points = sum(1 for row in all_children if row["story_points"] is not None)
    basis = (
        "points"
        if jira.story_points_field and all_children
        and with_points / len(all_children) > POINTS_COVERAGE
        else "count"
    )

    window_start = clock.iso(now - timedelta(weeks=trailing_weeks))
    rows: list[EpicRow] = []
    for parent in parents:
        epic = con.execute(
            "SELECT key, summary, url, labels FROM jira_issues WHERE key = ?",
            (parent["parent_key"],),
        ).fetchone()
        children = [row for row in all_children if row["parent_key"] == parent["parent_key"]]

        total = sum(weight(row, basis) for row in children)
        done = sum(weight(row, basis) for row in children if row["status_category"] == "Done")
        recent = sum(
            weight(row, basis)
            for row in children
            if row["first_done_at"] and row["first_done_at"] >= window_start
        )
        remaining = max(0.0, total - done)

        if remaining <= 0:
            projection_text, projection_state, drift = "complete", "none", None
        elif recent <= 0:
            projection_text, projection_state, drift = "no recent progress", "none", None
        else:
            rate_per_day = recent / (trailing_weeks * 7)
            finish = now + timedelta(days=remaining / rate_per_day)
            stamp = f"~{finish.day} {finish.strftime('%b')}"
            due = _due_date(epic["labels"] if epic else None)
            if due is None:
                projection_text, projection_state, drift = f"{stamp} · on track", "on_track", None
            else:
                drift = round((finish - due).total_seconds() / 86400)
                if drift > 0:
                    projection_text, projection_state = f"{stamp} · {drift}d past due", "drift"
                else:
                    projection_text, projection_state = f"{stamp} · on track", "on_track"

        rows.append(
            EpicRow(
                key=parent["parent_key"],
                name=(epic["summary"] if epic else parent["parent_key"]) or parent["parent_key"],
                url=(epic["url"] if epic else "") or "",
                done=done,
                total=total,
                completion_text=f"{_number(done)} / {_number(total)}",
                progress=charts.progress_bar(done, total),
                projection_text=projection_text,
                projection_state=projection_state,
                drift_days=drift,
                activity_text=f"{_number(recent)} in last {trailing_weeks}w",
            )
        )
    return EpicsSection(
        rows=rows,
        basis=basis,
        basis_label="by story points" if basis == "points" else "by issue count",
        empty_message=None,
    )
