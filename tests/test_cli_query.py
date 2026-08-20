import json
from pathlib import Path

from typer.testing import CliRunner

from waypoint.cli import app

runner = CliRunner()


def seeded(project_dir: Path) -> Path:
    runner.invoke(app, ["build", "--dir", str(project_dir)])
    return project_dir


def test_query_returns_json_rows(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(
        app, ["query", "SELECT id, name FROM people ORDER BY id", "--dir", str(project_dir)]
    )
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert {"id": "alex-rivera", "name": "Alex Rivera"} in rows


def test_query_rejects_a_write(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(app, ["query", "DELETE FROM people", "--dir", str(project_dir)])
    assert result.exit_code == 1
    assert "read-only" in result.stdout


def test_query_rejects_multiple_statements(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(
        app, ["query", "SELECT 1; DROP TABLE people", "--dir", str(project_dir)]
    )
    assert result.exit_code == 1


def test_query_reports_a_sql_error_without_a_traceback(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(app, ["query", "SELECT nope FROM people", "--dir", str(project_dir)])
    assert result.exit_code == 1
    assert "Traceback" not in result.stdout
    assert "nope" in result.stdout


def test_query_before_any_build_says_so(tmp_path: Path):
    (tmp_path / ".waypoint").mkdir()
    result = runner.invoke(app, ["query", "SELECT 1", "--dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "waypoint sync" in result.stdout


def test_table_format_prints_a_header(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(
        app, ["query", "SELECT id FROM people", "--format", "table", "--dir", str(project_dir)]
    )
    assert result.stdout.splitlines()[0].strip() == "id"
