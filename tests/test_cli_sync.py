from pathlib import Path

from typer.testing import CliRunner

from waypoint.cli import app

runner = CliRunner()


def test_build_rebuilds_from_existing_raw(project_dir: Path, monkeypatch):
    monkeypatch.chdir(project_dir)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 0
    assert (project_dir / ".waypoint" / "index.db").exists()
    assert "index" in result.stdout.lower()


def test_build_reports_a_missing_config_without_a_traceback(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "config.toml" in result.stdout
    assert "Traceback" not in result.stdout


def test_sync_reports_missing_secrets_by_variable_name(project_dir: Path, monkeypatch):
    monkeypatch.chdir(project_dir)
    for name in ("WAYPOINT_GITHUB_TOKEN", "WAYPOINT_JIRA_EMAIL", "WAYPOINT_JIRA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "WAYPOINT_GITHUB_TOKEN" in result.stdout


def test_doctor_exits_nonzero_when_a_check_fails(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "config" in result.stdout
