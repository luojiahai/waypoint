import json
import subprocess
from pathlib import Path

import pytest

from waypoint import clock
from waypoint import skills_runner
from waypoint.store.reports import ReportStore

SIDECAR = {
    "skill": "waypoint:delivery-risk",
    "generated_at": "2026-08-19T09:40:00Z",
    "window": {"from": "2026-08-05", "to": "2026-08-19"},
    "inputs_digest": "sha256:abc",
    "items": [{
        "severity": "high", "title": "T", "body": "B",
        "evidence": [{"type": "pull_request", "ref": "PR #482", "url": "https://ghe/pr/482"}],
        "question": "Q?",
    }],
}


def fake_run_writing(project_dir: Path, sidecar=SIDECAR, returncode=0, stderr=""):
    def runner(cmd, **kwargs):
        if returncode == 0:
            ReportStore(project_dir / ".waypoint").write(
                "waypoint:delivery-risk", sidecar, "# Delivery risk\n",
                at=clock.now(),
            )
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    return runner


def test_every_shipped_skill_is_registered():
    assert set(skills_runner.SKILLS) == {
        "delivery-risk", "delivery-review", "one-on-one-prep",
        "workload-review", "growth-review",
    }


def test_person_scoped_skills_are_marked_as_such():
    assert skills_runner.SKILLS["one-on-one-prep"].scope == "person"
    assert skills_runner.SKILLS["growth-review"].scope == "person"
    assert skills_runner.SKILLS["delivery-risk"].scope == "global"


def test_a_successful_run_returns_the_written_report(project_dir: Path):
    outcome = skills_runner.run_skill(
        project_dir, "delivery-risk", subprocess_run=fake_run_writing(project_dir)
    )
    assert outcome.ok is True
    assert outcome.report.items[0].title == "T"


def test_the_command_invokes_the_skill_headlessly_in_the_project_directory(project_dir: Path):
    seen = {}

    def runner(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = str(kwargs.get("cwd"))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    skills_runner.run_skill(project_dir, "delivery-risk", subprocess_run=runner)
    assert "/waypoint:delivery-risk" in " ".join(seen["cmd"])
    assert seen["cwd"] == str(project_dir)


def test_the_subprocess_never_receives_waypoint_secrets(project_dir: Path, monkeypatch):
    # The skill has no legitimate need for Waypoint's own GitHub/Jira
    # credentials -- it reads `.waypoint/` through `waypoint query`, never
    # calling GitHub or Jira itself. This is the one place in the product
    # where control passes to an external process, so least privilege
    # matters here especially: the child gets everything else (PATH and
    # friends) but never the WAYPOINT_* secrets, even when they're exported
    # as real environment variables rather than living only in `.env`.
    monkeypatch.setenv("WAYPOINT_GITHUB_TOKEN", "ghp_should_never_leave_this_process")
    monkeypatch.setenv("WAYPOINT_JIRA_TOKEN", "jira_should_never_leave_this_process")
    monkeypatch.setenv("WAYPOINT_JIRA_EMAIL", "someone@example.com")
    seen = {}

    def runner(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    skills_runner.run_skill(project_dir, "delivery-risk", subprocess_run=runner)

    env = seen["env"]
    assert env is not None
    assert "WAYPOINT_GITHUB_TOKEN" not in env
    assert "WAYPOINT_JIRA_TOKEN" not in env
    assert "WAYPOINT_JIRA_EMAIL" not in env
    assert "PATH" in env  # the child still gets what it needs to run at all


def test_a_person_scoped_skill_passes_the_person(project_dir: Path):
    seen = {}

    def runner(cmd, **kwargs):
        seen["cmd"] = " ".join(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    skills_runner.run_skill(
        project_dir, "one-on-one-prep", person_id="alex-rivera", subprocess_run=runner
    )
    assert "alex-rivera" in seen["cmd"]


def test_a_nonzero_exit_is_reported_not_raised(project_dir: Path):
    outcome = skills_runner.run_skill(
        project_dir, "delivery-risk",
        subprocess_run=fake_run_writing(project_dir, returncode=2, stderr="model unavailable"),
    )
    assert outcome.ok is False
    assert "model unavailable" in outcome.message


def test_a_timeout_is_reported_not_raised(project_dir: Path):
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, timeout=5)

    outcome = skills_runner.run_skill(project_dir, "delivery-risk", subprocess_run=runner)
    assert outcome.ok is False
    assert "timed out" in outcome.message


def test_a_missing_claude_binary_is_reported_not_raised(project_dir: Path):
    def runner(cmd, **kwargs):
        raise FileNotFoundError("claude")

    outcome = skills_runner.run_skill(project_dir, "delivery-risk", subprocess_run=runner)
    assert outcome.ok is False
    assert "Claude Code" in outcome.message


def test_an_unknown_skill_is_rejected_before_any_subprocess(project_dir: Path):
    def runner(cmd, **kwargs):
        raise AssertionError("should not be called")

    outcome = skills_runner.run_skill(project_dir, "rm-rf", subprocess_run=runner)
    assert outcome.ok is False
    assert "Unknown skill" in outcome.message


def test_no_module_other_than_skills_runner_imports_subprocess():
    offenders = [
        path
        for path in Path("waypoint").rglob("*.py")
        if "subprocess" in path.read_text() and path.name != "skills_runner.py"
    ]
    assert offenders == []


def test_claude_available_is_false_when_the_binary_is_absent():
    assert skills_runner.claude_available(runner="definitely-not-a-real-binary") is False
