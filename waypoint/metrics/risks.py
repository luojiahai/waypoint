"""The rule-derived risk register.

Always present and independent of any skill (§11). Each risk carries what, why,
and a link to the underlying item. Skill-generated risks merge into the same
list via `merge_skill_risks` and are badged by origin -- the web layer only
calls it and renders the result (§6: the web layer never computes).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from waypoint import clock
from waypoint.config import Config
from waypoint.metrics import board, epics
from waypoint.roster import UNATTRIBUTED
from waypoint.store.derive import days_between

if TYPE_CHECKING:  # `store.reports` touches the filesystem; only its dataclass is used
    from waypoint.store.reports import ReportItem

SEVERITY_ORDER = {"high": 0, "med": 1, "low": 2}

# No configured threshold exists for epic due-date drift (unlike the day-count
# thresholds in Config.thresholds), so a week is used as the one threshold-width
# for `escalate` -- the same weekly cadence epics.py already projects against.
EPIC_DRIFT_WIDTH_DAYS = 7


@dataclass(frozen=True)
class Evidence:
    kind: str
    ref: str
    url: str


@dataclass(frozen=True)
class Risk:
    rule: str
    severity: str
    title: str
    detail: str
    evidence: list[Evidence]
    age_days: float
    age_text: str
    origin: str = "rule"


@dataclass(frozen=True)
class RiskRegister:
    items: list[Risk] = field(default_factory=list)
    evaluated: int = 0
    empty_message: str | None = None


def merge_skill_risks(register: RiskRegister, items: Sequence["ReportItem"]) -> list[Risk]:
    """Merge a skill report's items into the rule-derived register, sorted.

    A skill report carries no per-item age, so merged rows sort as if age
    were 0 -- ties within a severity fall back to `rule`, same as
    rule-derived risks. Skill items are badged `origin="skill"`; everything
    else keeps `origin="rule"` (§11).
    """
    merged = list(register.items)
    for item in items:
        merged.append(
            Risk(
                rule="skill", severity=item.severity, title=item.title,
                detail=item.body,
                evidence=[
                    Evidence(source.get("type", "item"), source.get("ref", ""),
                             source.get("url", ""))
                    for source in item.evidence
                ],
                age_days=0.0, age_text="", origin="skill",
            )
        )
    merged.sort(key=lambda risk: (SEVERITY_ORDER[risk.severity], -risk.age_days, risk.rule))
    return merged


def escalate(threshold_widths_over: float) -> str:
    """Severity rises with how far past the threshold an item is."""
    if threshold_widths_over >= 2.0:
        return "high"
    if threshold_widths_over >= 1.0:
        return "med"
    return "low"


def _age(days: float) -> str:
    return f"{days:.0f}d"


def rule_risks(con: sqlite3.Connection, cfg: Config, *, now: datetime) -> RiskRegister:
    now_iso = clock.iso(now)
    thresholds = cfg.thresholds
    items: list[Risk] = []
    evaluated = 0

    for row in con.execute(
        "SELECT p.id, p.title, p.url, f.review_wait_current FROM pull_requests p "
        "JOIN pr_flow f ON f.pr_id = p.id WHERE p.state = 'OPEN'"
    ):
        evaluated += 1
        wait = row["review_wait_current"]
        if wait is None:
            continue
        days = wait / 24
        if days < thresholds.pr_review_wait_days:
            continue
        items.append(
            Risk(
                rule="pr_no_review",
                severity=escalate(days / thresholds.pr_review_wait_days - 1),
                title=f"{row['id']} has had no review for {_age(days)}",
                detail=row["title"] or "",
                evidence=[Evidence("pull_request", row["id"], row["url"] or "")],
                age_days=round(days, 1),
                age_text=_age(days),
            )
        )

    for row in con.execute(
        "SELECT p.id, p.title, p.url, MAX(v.submitted_at) AS approved_at FROM pull_requests p "
        "JOIN pr_reviews v ON v.pr_id = p.id "
        "WHERE p.state = 'OPEN' AND v.state = 'APPROVED' GROUP BY p.id"
    ):
        days = days_between(row["approved_at"], now_iso) or 0.0
        if days < thresholds.pr_approved_unmerged_days:
            continue
        items.append(
            Risk(
                rule="pr_approved_unmerged",
                severity=escalate(days / thresholds.pr_approved_unmerged_days - 1),
                title=f"{row['id']} approved {_age(days)} ago and still unmerged",
                detail=row["title"] or "",
                evidence=[Evidence("pull_request", row["id"], row["url"] or "")],
                age_days=round(days, 1),
                age_text=_age(days),
            )
        )

    linked_activity = {
        row["issue_key"]: row["stamp"]
        for row in con.execute(
            "SELECT l.issue_key, MAX(COALESCE(p.updated_at, p.created_at)) AS stamp "
            "FROM issue_pr_links l JOIN pull_requests p ON p.id = l.pr_id GROUP BY l.issue_key"
        )
    }

    in_flight = board.in_flight(con, now=now)
    columns = {column.name: column for column in board.columns(con)}

    # The oldest in-flight item per column, in the order `board.in_flight`
    # already sorts by (-age_days, key) -- so the first item seen for a column
    # is that column's oldest (§ Override 2: the WIP-limit risk needs a real age).
    oldest_by_column: dict[str, board.InFlightItem] = {}
    for item in in_flight:
        oldest_by_column.setdefault(item.column, item)

    for item in in_flight:
        evaluated += 1
        issue = con.execute(
            "SELECT flagged, assignee_person_id, parent_key FROM jira_issues WHERE key = ?",
            (item.key,),
        ).fetchone()
        evidence = [Evidence("issue", item.key, item.url)]

        if issue["flagged"]:
            items.append(
                Risk("issue_flagged", "high", f"{item.key} is flagged", item.summary,
                     evidence, item.age_days, item.age_text)
            )

        pr_stamp = linked_activity.get(item.key)
        pr_idle = days_between(pr_stamp, now_iso) if pr_stamp else None
        stalled_enough = item.stalled_days >= thresholds.issue_stalled_days
        pr_quiet = pr_idle is None or pr_idle >= thresholds.issue_stalled_days
        if stalled_enough and pr_quiet:
            items.append(
                Risk(
                    "issue_stalled",
                    escalate(item.stalled_days / thresholds.issue_stalled_days - 1),
                    f"{item.key} has not moved for {_age(item.stalled_days)}",
                    f"{item.summary} · {item.column}",
                    evidence, item.stalled_days, _age(item.stalled_days),
                )
            )

        if item.assignee_id in (None, "", UNATTRIBUTED):
            items.append(
                Risk("issue_unassigned", "med", f"{item.key} is in {item.column} with no assignee",
                     item.summary, evidence, item.age_days, item.age_text)
            )

        if item.age_days >= thresholds.issue_aging_days:
            items.append(
                Risk(
                    "issue_aging",
                    escalate(item.age_days / thresholds.issue_aging_days - 1),
                    f"{item.key} has been in flight for {item.age_text}",
                    f"{item.summary} · {item.column}",
                    evidence, item.age_days, item.age_text,
                )
            )

        reopened = con.execute(
            "SELECT COUNT(*) AS n FROM issue_transitions "
            "WHERE issue_key = ? AND field = 'status' AND from_value = 'Done'",
            (item.key,),
        ).fetchone()["n"]
        if reopened > 1:
            items.append(
                Risk("issue_reopened", "med", f"{item.key} has been reopened {reopened} times",
                     item.summary, evidence, item.age_days, item.age_text)
            )

    for column in columns.values():
        if column.no_limit:
            continue  # a column with no limit is never over limit (§11)
        evaluated += 1
        if column.over:
            # The oldest in-flight item in this column, if one exists. A column
            # can be over its limit on the strength of issues whose status_id
            # maps into it but whose status_category isn't "In Progress" (so
            # they never appear in `in_flight`) -- an honest "—" beats a
            # fabricated 0.0/"" age in that edge case (§4, Override 2).
            oldest = oldest_by_column.get(column.name)
            items.append(
                Risk(
                    "column_over_limit", "med",
                    f"{column.name} is over its WIP limit ({column.count} of {column.limit})",
                    "Work started faster than it is finishing.",
                    [Evidence("column", column.name, "")],
                    age_days=oldest.age_days if oldest else 0.0,
                    age_text=oldest.age_text if oldest else "—",
                )
            )

    epic_owners: dict[str, set[str]] = {}
    for item in in_flight:
        parent = con.execute(
            "SELECT parent_key FROM jira_issues WHERE key = ?", (item.key,)
        ).fetchone()["parent_key"]
        if parent:
            epic_owners.setdefault(parent, set()).add(item.assignee_id or UNATTRIBUTED)
    for epic_key, owners in sorted(epic_owners.items()):
        children = [i for i in in_flight if i.key in {
            row["key"] for row in con.execute(
                "SELECT key FROM jira_issues WHERE parent_key = ?", (epic_key,)
            )
        }]
        evaluated += 1
        if len(owners) == 1 and len(children) > 1:
            owner_id = next(iter(owners))
            name = con.execute(
                "SELECT name FROM people WHERE id = ?", (owner_id,)
            ).fetchone()
            items.append(
                Risk(
                    "epic_single_owner", "med",
                    f"All in-flight work on {epic_key} sits with one person",
                    f"{len(children)} in-flight children, all assigned to "
                    f"{name['name'] if name else owner_id}.",
                    [Evidence("issue", epic_key, "")],
                    max(child.age_days for child in children),
                    _age(max(child.age_days for child in children)),
                )
            )

    # Epic projected to finish past its due date -- escalates with drift
    # (§11's missing tenth rule). Task 17's `epics()` already computes
    # `projection_state` and `drift_days`; a state of "none" means no
    # projection was computable (complete, or no recent progress) so there is
    # nothing to fire here. This rule is not one of the brief's original four
    # `evaluated` sites (it didn't exist in the brief at all), so -- per the
    # per-item definition `evaluated` was reverted to -- it does not add to
    # `evaluated`, matching how the brief's own `pr_approved_unmerged` loop
    # never did either.
    for row in epics.epics(con, now=now, jira=cfg.jira).rows:
        if row.projection_state != "drift" or row.drift_days is None:
            continue
        drift = float(row.drift_days)
        items.append(
            Risk(
                "epic_drift",
                escalate(drift / EPIC_DRIFT_WIDTH_DAYS - 1),
                f"{row.key} is projected to finish {row.drift_days}d past its due date",
                f"{row.name} · {row.projection_text}",
                [Evidence("issue", row.key, row.url)],
                age_days=drift,
                age_text=_age(drift),
            )
        )

    items.sort(key=lambda risk: (SEVERITY_ORDER[risk.severity], -risk.age_days, risk.rule))
    return RiskRegister(
        items=items,
        evaluated=evaluated,
        empty_message=None if items else "Nothing crossed a threshold.",
    )
