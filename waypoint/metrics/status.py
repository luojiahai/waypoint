"""Which panels are demoted, and what the demotion says.

Showing a chart that quietly omits half its data is the only failure mode that
causes real harm, because the user would act on it (§4). A panel reading any
entity that is not `ok` is demoted, and the reason line carries the same
information at full contrast as the dimmed figures beside it (UI§6, UI§9).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waypoint.store.manifest import Manifest

GITHUB_PRS = ("github/pull_requests", "github/reviews", "github/review_requests")
JIRA_ISSUES = ("jira/issues", "jira/changelogs")
BOARD = ("jira/board_config", "jira/issues")
EVERYTHING = GITHUB_PRS + JIRA_ISSUES + ("jira/board_config",)

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
