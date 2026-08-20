"""The only module that knows the Claude CLI exists.

Everything about subprocessing Claude Code is quarantined here so the dashboard
degrades cleanly to read-only when it is absent (§6, §15).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from waypoint.store.reports import Report, ReportStore

DEFAULT_TIMEOUT = 300


@dataclass(frozen=True)
class SkillSpec:
    name: str
    slug: str
    produces: str
    scope: str


SKILLS: dict[str, SkillSpec] = {
    "delivery-risk": SkillSpec(
        "waypoint:delivery-risk", "delivery-risk",
        "Ranked risk register with evidence and a suggested next move", "global",
    ),
    "delivery-review": SkillSpec(
        "waypoint:delivery-review", "delivery-review",
        "What is close to landing, what is aging, and where flow is blocked", "global",
    ),
    "one-on-one-prep": SkillSpec(
        "waypoint:one-on-one-prep", "one-on-one-prep",
        "Per-person brief of what to ask about", "person",
    ),
    "workload-review": SkillSpec(
        "waypoint:workload-review", "workload-review",
        "Where load is uneven across the team", "global",
    ),
    "growth-review": SkillSpec(
        "waypoint:growth-review", "growth-review",
        "How a person's work mix has shifted over time", "person",
    ),
}


@dataclass(frozen=True)
class RunOutcome:
    ok: bool
    message: str
    report: Report | None = None


def claude_available(runner: str = "claude") -> bool:
    return shutil.which(runner) is not None


def run_skill(
    project_dir: Path,
    slug: str,
    *,
    person_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    runner: str = "claude",
    subprocess_run=subprocess.run,
) -> RunOutcome:
    spec = SKILLS.get(slug)
    if spec is None:
        return RunOutcome(False, f"Unknown skill {slug!r}.")

    prompt = f"/{spec.name}"
    if spec.scope == "person":
        if not person_id:
            return RunOutcome(False, f"{spec.name} needs a person.")
        prompt = f"{prompt} {person_id}"

    command = [runner, "-p", prompt, "--permission-mode", "acceptEdits"]
    try:
        completed = subprocess_run(
            command, cwd=str(project_dir), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return RunOutcome(
            False,
            "Claude Code is not installed, so generated analysis is unavailable. "
            "The rest of the dashboard is unaffected.",
        )
    except subprocess.TimeoutExpired:
        return RunOutcome(False, f"{spec.name} timed out after {timeout}s.")

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        return RunOutcome(False, f"{spec.name} failed: {detail}")

    report = ReportStore(Path(project_dir) / ".waypoint").latest(spec.name, person_id=person_id)
    if report is None:
        return RunOutcome(False, f"{spec.name} produced no report.")
    return RunOutcome(True, "complete", report)
