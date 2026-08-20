"""Which panels are demoted, and what the demotion says.

Showing a chart that quietly omits half its data is the only failure mode that
causes real harm, because the user would act on it (§4). A panel reading any
entity that is not `ok` is demoted, and the reason line carries the same
information at full contrast as the dimmed figures beside it (UI§6, UI§9).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from waypoint import clock

if TYPE_CHECKING:
    from waypoint.store.manifest import Manifest

GITHUB_PRS = ("github/pull_requests", "github/reviews", "github/review_requests")
JIRA_ISSUES = ("jira/issues", "jira/changelogs")
BOARD = ("jira/board_config", "jira/issues")
EVERYTHING = GITHUB_PRS + JIRA_ISSUES + ("jira/board_config",)

# The badge vocabulary, ordered by severity. `ok`/`partial`/`failed` are the
# manifest's own statuses in the manifest's own order (store.manifest.RANK);
# `stale` is the one state that exists only here, and sits just above `ok`
# because a report predating the current sync is a freshness problem, not a
# hole in the data (UI§6).
SEVERITY = {"ok": 0, "stale": 1, "partial": 2, "failed": 3}

_UNAFFECTED = {
    "github": "Jira panels are unaffected.",
    "jira": "GitHub panels are unaffected.",
}


@dataclass(frozen=True)
class DataStatus:
    state: str
    badge: str | None = None
    reason: str | None = None

    @property
    def demoted(self) -> bool:
        return self.state != "ok"


OK_STATUS = DataStatus(state="ok")


def worst_of(*statuses: "DataStatus | None") -> DataStatus:
    """The most severe of the statuses given.

    A panel can be degraded for more than one reason at once -- the risk
    register reads every entity *and* carries a skill report -- and only the
    worst of them may be shown, because a demotion that loses to a milder one
    silently un-demotes the panel (§4). `None` means "nothing to say".
    """
    worst = OK_STATUS
    for status in statuses:
        if status is not None and SEVERITY[status.state] > SEVERITY[worst.state]:
            worst = status
    return worst


def _sources(entities: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for key in entities:
        source = key.split("/", 1)[0]
        if source not in seen:
            seen.append(source)
    return seen


def _unaffected_clause(entities: Sequence[str]) -> str:
    """The "X panels are unaffected" clause, or "" when nothing is true to say.

    A panel that spans more than one source cannot claim any other source is
    unaffected, because the failing group includes that source too.
    """
    sources_present = _sources(entities)
    if len(sources_present) != 1:
        return ""
    return _UNAFFECTED.get(sources_present[0], "")


def panel_status(manifest: "Manifest", entities: Sequence[str]) -> DataStatus:
    state = manifest.status_for(entities)
    if state == "ok":
        return OK_STATUS

    known = [manifest.entities[key] for key in entities if key in manifest.entities]
    missing = [key for key in entities if key not in manifest.entities]
    errors = sorted({e.error for e in known if e.error})
    error_text = "; ".join(errors) if errors else "no error recorded"
    arrived = sum(e.count for e in known if e.status != "ok")
    other = _unaffected_clause(entities)

    if state == "failed" and missing:
        reason = f"{', '.join(missing)} has never synced."
        if errors:
            sources = " and ".join(_sources(entities))
            reason += f" {sources} failed: {error_text}."
        reason += (
            " Press Sync to fetch it, or run `waypoint doctor` if configuration is incomplete."
        )
        return DataStatus(state="failed", badge="FAILED", reason=reason)
    if state == "failed":
        sources = " and ".join(_sources(entities))
        return DataStatus(
            state="failed",
            badge="FAILED",
            reason=f"{sources} failed: {error_text}. {other}".strip(),
        )
    return DataStatus(
        state="partial",
        badge="PARTIAL",
        reason=(
            f"{arrived} records arrived before the fetch stopped: {error_text}. "
            f"Sync again to complete it."
        ),
    )


def stale_status(inputs_digest: str, manifest: "Manifest", generated_at: str) -> DataStatus:
    """A report predating the current sync is not a data problem (UI§6)."""
    if inputs_digest == manifest.digest():
        return OK_STATUS
    return DataStatus(
        state="stale",
        badge="STALE",
        reason=f"Generated {generated_at}; the underlying data has changed since.",
    )


@dataclass(frozen=True)
class SyncLabel:
    """The chrome's freshness line: the words, and the severity that colours them."""

    text: str
    state: str


def sync_label(manifest: "Manifest", now: datetime) -> SyncLabel:
    """When the last sync ran and how it went, as one already-worded line (UI§5).

    The elapsed-time arithmetic lives here rather than in `web/` because the
    result is text the user reads, and the web layer renders and never
    computes (§6).
    """
    run = manifest.last_run()
    if run is None or run.finished_at is None:
        return SyncLabel("never synced", "ok")
    stamp = clock.parse(run.finished_at)
    clock_text = stamp.strftime("%H:%M")
    if run.status == "failed":
        return SyncLabel(f"last sync failed · {clock_text}", "failed")
    if run.status == "partial":
        return SyncLabel(f"last sync partial · {clock_text}", "partial")
    hours = (now - stamp).total_seconds() / 3600
    ago = f"{hours:.0f}h ago" if hours >= 1 else f"{hours * 60:.0f}m ago"
    return SyncLabel(f"synced {clock_text} · {ago}", "ok")
