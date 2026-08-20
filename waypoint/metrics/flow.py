"""Flow: review latency, cycle time, WIP, throughput.

Reported as median and p75 over rolling windows, with no target lines and no
red/green thresholds — a cycle time with a goal line becomes a number to game
(§10). Distributions and trends only.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from waypoint import clock
from waypoint.metrics import charts
from waypoint.store.derive import IN_PROGRESS, _category_for, _category_map


@dataclass(frozen=True)
class Distribution:
    median: float | None
    p75: float | None
    count: int


@dataclass(frozen=True)
class FlowPanel:
    label: str
    unit: str
    median_text: str
    p75_text: str
    spark: charts.Spark
    count: int


@dataclass(frozen=True)
class ThroughputPanel:
    current: int
    previous: int
    summary: str
    spark: charts.BarSpark
    weeks: list[int]


@dataclass(frozen=True)
class WipPanel:
    current: int
    median: float | None
    spark: charts.Spark
    series: list[int]


@dataclass(frozen=True)
class OpenPR:
    id: str
    title: str
    repo_id: str
    review_wait_text: str
    url: str


def distribution(values: Sequence[float | None]) -> Distribution:
    real = sorted(v for v in values if v is not None)
    if not real:
        return Distribution(median=None, p75=None, count=0)
    if len(real) == 1:
        return Distribution(median=real[0], p75=real[0], count=1)
    # `method="inclusive"` is this project's one quartile convention -- it is
    # what review_latency's and issue_cycle_time's panels are built and tested
    # against (e.g. p75 of [4,8,12,40]h reads as 19h). Don't swap to
    # "exclusive" or a hinges/Tukey definition to satisfy some other expected
    # value; that changes every panel's p75, not just the one test.
    quantiles = statistics.quantiles(real, n=4, method="inclusive")
    return Distribution(median=statistics.median(real), p75=quantiles[2], count=len(real))


def _format(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}{unit}" if unit == "h" else f"{value:.1f}{unit}"


def _week_edges(now: datetime, weeks: int) -> list[tuple[str, str]]:
    edges = []
    for index in range(weeks, 0, -1):
        end = now - timedelta(days=7 * (index - 1))
        start = end - timedelta(days=7)
        edges.append((clock.iso(start), clock.iso(end)))
    return edges


def _panel(
    label: str, unit: str, rows: Sequence[tuple[str | None, float | None]],
    now: datetime, weeks: int, divisor: float,
) -> FlowPanel:
    values = [None if v is None else v / divisor for _, v in rows]
    overall = distribution(values)
    medians: list[float | None] = []
    p75s: list[float | None] = []
    for start, end in _week_edges(now, weeks):
        window = [
            None if value is None else value / divisor
            for stamp, value in rows
            if stamp and start <= stamp < end
        ]
        weekly = distribution(window)
        medians.append(weekly.median)
        p75s.append(weekly.p75)
    return FlowPanel(
        label=label,
        unit=unit,
        median_text=_format(overall.median, unit),
        p75_text=_format(overall.p75, unit),
        spark=charts.sparkline(medians, p75s),
        count=overall.count,
    )


def review_latency(con: sqlite3.Connection, *, now: datetime, weeks: int = 12) -> FlowPanel:
    rows = [
        (row["ready_at"] or row["created_at"], row["time_to_first_review"])
        for row in con.execute(
            "SELECT p.ready_at, p.created_at, f.time_to_first_review "
            "FROM pr_flow f JOIN pull_requests p ON p.id = f.pr_id "
            "WHERE f.time_to_first_review IS NOT NULL"
        )
    ]
    return _panel("PR review latency", "h", rows, now, weeks, divisor=1.0)


def issue_cycle_time(con: sqlite3.Connection, *, now: datetime, weeks: int = 12) -> FlowPanel:
    rows = [
        (row["first_done_at"], row["cycle_time"])
        for row in con.execute(
            "SELECT first_done_at, cycle_time FROM issue_flow WHERE cycle_time IS NOT NULL"
        )
    ]
    return _panel("Issue cycle time", "d", rows, now, weeks, divisor=24.0)


def _wip_at(transitions: Sequence[sqlite3.Row], moment: str, categories: dict[str, str]) -> int:
    """Resolve each transition's category through `derive._category_for`.

    A bare `categories.get(to_value, IN_PROGRESS)` would silently miscount: a
    status name that no issue currently occupies (a retired "Closed" after
    everything holding it moved on, say) is absent from `categories`, which is
    built from *current* `jira_issues` rows. `_category_for` still resolves it
    via `_CATEGORY_HINTS` before falling back to IN_PROGRESS, so WIP isn't
    inflated by history the current snapshot no longer carries.
    """
    state: dict[str, str] = {}
    for transition in transitions:
        if transition["changed_at"] > moment:
            break
        state[transition["issue_key"]] = _category_for(transition["to_value"], categories)
    return sum(1 for category in state.values() if category == IN_PROGRESS)


def wip_series(con: sqlite3.Connection, *, now: datetime, weeks: int = 12) -> WipPanel:
    """WIP is reconstructed from `issue_transitions` at each point in time (§10)."""
    transitions = con.execute(
        "SELECT issue_key, to_value, changed_at FROM issue_transitions "
        "WHERE field = 'status' ORDER BY changed_at"
    ).fetchall()
    categories = _category_map(con)
    series = [
        _wip_at(transitions, end, categories) for _, end in _week_edges(now, weeks)
    ]
    current = _wip_at(transitions, clock.iso(now), categories)
    return WipPanel(
        current=current,
        median=float(statistics.median(series)) if series else None,
        spark=charts.sparkline([float(value) for value in series]),
        series=series,
    )


def open_prs(con: sqlite3.Connection) -> list[OpenPR]:
    """Every open PR, oldest review wait first (§10's queue-not-target reading)."""
    items: list[OpenPR] = []
    for row in con.execute(
        "SELECT p.id, p.title, p.repo_id, p.url, f.review_wait_current "
        "FROM pull_requests p LEFT JOIN pr_flow f ON f.pr_id = p.id "
        "WHERE p.state = 'OPEN' ORDER BY f.review_wait_current DESC NULLS LAST, p.id"
    ):
        wait = row["review_wait_current"]
        items.append(
            OpenPR(
                id=row["id"],
                title=row["title"] or "",
                repo_id=row["repo_id"],
                review_wait_text=f"{wait / 24:.0f}d waiting" if wait is not None else "reviewed",
                url=row["url"] or "",
            )
        )
    return items


def throughput(
    con: sqlite3.Connection, *, now: datetime, window_days: int = 14, weeks: int = 12
) -> ThroughputPanel:
    """A team-level count of issues reaching Done. Never attributed to anyone."""
    rows = con.execute(
        "SELECT first_done_at FROM issue_flow WHERE first_done_at IS NOT NULL"
    ).fetchall()
    stamps = [row["first_done_at"] for row in rows]

    def count_between(start: datetime, end: datetime) -> int:
        low, high = clock.iso(start), clock.iso(end)
        return sum(1 for stamp in stamps if low <= stamp < high)

    window = timedelta(days=window_days)
    current = count_between(now - window, now)
    previous = count_between(now - window * 2, now - window)
    weekly = [count_between(clock.parse(a), clock.parse(b)) for a, b in _week_edges(now, weeks)]
    return ThroughputPanel(
        current=current,
        previous=previous,
        summary=(
            f"{current} done in last {window_days}d · {previous} in the {window_days}d before"
        ),
        spark=charts.bar_spark(weekly),
        weeks=weekly,
    )
