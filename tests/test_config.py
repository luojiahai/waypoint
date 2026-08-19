from pathlib import Path

import pytest

from waypoint.config import load_config, load_secrets
from waypoint.errors import ConfigError


def test_loads_every_section(waypoint_root: Path):
    cfg = load_config(waypoint_root)
    assert cfg.github.base_url == "https://ghe.corp.example.com"
    assert cfg.github.repos == ("platform/api", "platform/web")
    assert cfg.github.bot_logins == ("dependabot", "renovate")
    assert cfg.jira.board_id == 42
    assert cfg.jira.story_points_field == "customfield_10016"
    assert cfg.sync.backfill_days == 90
    assert cfg.thresholds.issue_aging_days == 10
    assert [p.name for p in cfg.people] == ["Alex Rivera", "Bo Chen"]


def test_base_url_trailing_slash_is_stripped(waypoint_root: Path):
    text = (waypoint_root / "config.toml").read_text()
    (waypoint_root / "config.toml").write_text(
        text.replace("https://ghe.corp.example.com", "https://ghe.corp.example.com/")
    )
    assert load_config(waypoint_root).github.base_url == "https://ghe.corp.example.com"


def test_missing_config_names_the_path_and_the_fix(tmp_path: Path):
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / ".waypoint")
    assert "config.toml" in exc.value.message
    assert "waypoint doctor" in exc.value.message


def test_missing_required_key_is_reported_by_name(waypoint_root: Path):
    (waypoint_root / "config.toml").write_text('[github]\nbase_url = "https://x"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(waypoint_root)
    assert "jira" in exc.value.message


def test_empty_story_points_field_disables_point_metrics(waypoint_root: Path):
    text = (waypoint_root / "config.toml").read_text()
    (waypoint_root / "config.toml").write_text(text.replace('"customfield_10016"', '""'))
    assert load_config(waypoint_root).jira.story_points_field == ""


def test_work_mix_buckets_are_case_insensitive(waypoint_root: Path):
    mix = load_config(waypoint_root).work_mix
    assert mix.bucket_for("Story") == "feature"
    assert mix.bucket_for("bug") == "bug"
    assert mix.bucket_for("Support") == "toil"
    assert mix.bucket_for("Epic") == "other"


def test_config_object_exposes_no_token_attribute(waypoint_root: Path):
    cfg = load_config(waypoint_root)
    text = repr(cfg).lower()
    assert "token" not in text


def test_secrets_come_from_the_environment(tmp_path: Path):
    secrets = load_secrets(
        tmp_path,
        environ={
            "WAYPOINT_GITHUB_TOKEN": "gh-tok",
            "WAYPOINT_JIRA_EMAIL": "em@example.com",
            "WAYPOINT_JIRA_TOKEN": "jira-tok",
        },
    )
    assert secrets.github_token == "gh-tok"
    assert secrets.missing() == []


def test_dotenv_is_loaded_when_present_and_environment_wins(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "# comment\n"
        "WAYPOINT_GITHUB_TOKEN=from-file\n"
        'WAYPOINT_JIRA_EMAIL="quoted@example.com"\n'
        "\n"
        "WAYPOINT_JIRA_TOKEN=file-jira\n"
    )
    secrets = load_secrets(tmp_path, environ={"WAYPOINT_GITHUB_TOKEN": "from-env"})
    assert secrets.github_token == "from-env"
    assert secrets.jira_email == "quoted@example.com"
    assert secrets.jira_token == "file-jira"


def test_missing_secrets_are_listed_by_variable_name(tmp_path: Path):
    assert load_secrets(tmp_path, environ={}).missing() == [
        "WAYPOINT_GITHUB_TOKEN",
        "WAYPOINT_JIRA_EMAIL",
        "WAYPOINT_JIRA_TOKEN",
    ]
