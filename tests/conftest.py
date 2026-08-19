from pathlib import Path

import pytest

CONFIG_TOML = """
[github]
base_url = "https://ghe.corp.example.com"
repos = ["platform/api", "platform/web"]
bot_logins = ["dependabot", "renovate"]

[jira]
site = "example.atlassian.net"
project_key = "PROJ"
board_id = 42
story_points_field = "customfield_10016"

[sync]
backfill_days = 90

[thresholds]
pr_review_wait_days = 2
pr_approved_unmerged_days = 2
issue_stalled_days = 5
issue_aging_days = 10

[work_mix]
feature = ["Story", "Task"]
bug = ["Bug", "Defect"]
toil = ["Chore", "Support", "Maintenance"]

[[people]]
name = "Alex Rivera"
github_login = "arivera"
jira_account_id = "acct-alex"
active = true

[[people]]
name = "Bo Chen"
github_login = "bchen"
jira_account_id = "acct-bo"
active = true
"""


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A working directory containing a populated .waypoint/."""
    root = tmp_path / ".waypoint"
    root.mkdir()
    (root / "config.toml").write_text(CONFIG_TOML)
    return tmp_path


@pytest.fixture
def waypoint_root(project_dir: Path) -> Path:
    return project_dir / ".waypoint"
