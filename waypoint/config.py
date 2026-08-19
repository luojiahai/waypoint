"""Configuration from `.waypoint/config.toml`, secrets from the environment.

`Config` and `Secrets` are separate types on purpose: a token cannot leak into
a template, a log line, or a report if the object the renderer receives has no
field to hold one.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from waypoint.errors import ConfigError

SECRET_VARS = ("WAYPOINT_GITHUB_TOKEN", "WAYPOINT_JIRA_EMAIL", "WAYPOINT_JIRA_TOKEN")


@dataclass(frozen=True)
class GithubConfig:
    base_url: str
    repos: tuple[str, ...]
    bot_logins: tuple[str, ...]


@dataclass(frozen=True)
class JiraConfig:
    site: str
    project_key: str
    board_id: int
    story_points_field: str


@dataclass(frozen=True)
class SyncConfig:
    backfill_days: int


@dataclass(frozen=True)
class Thresholds:
    pr_review_wait_days: int
    pr_approved_unmerged_days: int
    issue_stalled_days: int
    issue_aging_days: int


@dataclass(frozen=True)
class WorkMix:
    feature: tuple[str, ...]
    bug: tuple[str, ...]
    toil: tuple[str, ...]

    def bucket_for(self, issue_type: str) -> str:
        key = (issue_type or "").casefold()
        for name, types in (("feature", self.feature), ("bug", self.bug), ("toil", self.toil)):
            if key in {t.casefold() for t in types}:
                return name
        return "other"


@dataclass(frozen=True)
class PersonConfig:
    name: str
    github_login: str
    jira_account_id: str
    active: bool


@dataclass(frozen=True)
class Config:
    root: Path
    github: GithubConfig
    jira: JiraConfig
    sync: SyncConfig
    thresholds: Thresholds
    work_mix: WorkMix
    people: tuple[PersonConfig, ...]


@dataclass(frozen=True)
class Secrets:
    github_token: str
    jira_email: str
    jira_token: str

    def missing(self) -> list[str]:
        present = {
            "WAYPOINT_GITHUB_TOKEN": self.github_token,
            "WAYPOINT_JIRA_EMAIL": self.jira_email,
            "WAYPOINT_JIRA_TOKEN": self.jira_token,
        }
        return [name for name in SECRET_VARS if not present[name]]


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ConfigError(
            f"config.toml is missing the [{name}] section. "
            f"Run `waypoint doctor` for the full list of required settings."
        )
    return value


def _require(section: dict, key: str, name: str):
    if key not in section:
        raise ConfigError(f"config.toml is missing `{key}` in the [{name}] section.")
    return section[key]


def load_config(root: Path) -> Config:
    """Load `.waypoint/config.toml`. `root` is the .waypoint directory."""
    path = root / "config.toml"
    if not path.exists():
        raise ConfigError(
            f"No config.toml at {path}. Create it, then run `waypoint doctor` to check it."
        )
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config.toml is not valid TOML: {exc}") from exc

    gh = _section(data, "github")
    jira = _section(data, "jira")
    sync = data.get("sync", {})
    thresholds = data.get("thresholds", {})
    mix = data.get("work_mix", {})

    people = tuple(
        PersonConfig(
            name=str(entry.get("name", "")).strip(),
            github_login=str(entry.get("github_login", "")).strip(),
            jira_account_id=str(entry.get("jira_account_id", "")).strip(),
            active=bool(entry.get("active", True)),
        )
        for entry in data.get("people", [])
    )
    for person in people:
        if not person.name:
            raise ConfigError("Every [[people]] entry needs a `name`.")

    return Config(
        root=root,
        github=GithubConfig(
            base_url=str(_require(gh, "base_url", "github")).rstrip("/"),
            repos=tuple(_require(gh, "repos", "github")),
            bot_logins=tuple(gh.get("bot_logins", ())),
        ),
        jira=JiraConfig(
            site=str(_require(jira, "site", "jira")).rstrip("/"),
            project_key=str(_require(jira, "project_key", "jira")),
            board_id=int(_require(jira, "board_id", "jira")),
            story_points_field=str(jira.get("story_points_field", "")),
        ),
        sync=SyncConfig(backfill_days=int(sync.get("backfill_days", 90))),
        thresholds=Thresholds(
            pr_review_wait_days=int(thresholds.get("pr_review_wait_days", 2)),
            pr_approved_unmerged_days=int(thresholds.get("pr_approved_unmerged_days", 2)),
            issue_stalled_days=int(thresholds.get("issue_stalled_days", 5)),
            issue_aging_days=int(thresholds.get("issue_aging_days", 10)),
        ),
        work_mix=WorkMix(
            feature=tuple(mix.get("feature", ())),
            bug=tuple(mix.get("bug", ())),
            toil=tuple(mix.get("toil", ())),
        ),
        people=people,
    )


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_secrets(project_dir: Path, environ: Mapping[str, str] | None = None) -> Secrets:
    """Environment first, then a gitignored `.env` in the project directory."""
    env = dict(environ) if environ is not None else dict(os.environ)
    dotenv = _read_dotenv(project_dir / ".env")
    resolved = {name: env.get(name) or dotenv.get(name, "") for name in SECRET_VARS}
    return Secrets(
        github_token=resolved["WAYPOINT_GITHUB_TOKEN"],
        jira_email=resolved["WAYPOINT_JIRA_EMAIL"],
        jira_token=resolved["WAYPOINT_JIRA_TOKEN"],
    )
