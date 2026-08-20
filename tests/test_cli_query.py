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


def test_query_rejects_pragma(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(
        app, ["query", "PRAGMA writable_schema=1", "--dir", str(project_dir)]
    )
    assert result.exit_code == 1


def test_query_rejects_attach(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(
        app,
        ["query", "ATTACH DATABASE '/tmp/evil.db' AS evil", "--dir", str(project_dir)],
    )
    assert result.exit_code == 1


def test_query_rejects_vacuum(project_dir: Path):
    seeded(project_dir)
    result = runner.invoke(app, ["query", "VACUUM", "--dir", str(project_dir)])
    assert result.exit_code == 1


def test_query_cte_write_bypasses_the_string_guard_but_readonly_connection_blocks_it(
    project_dir: Path,
):
    """`WITH x AS (...) DELETE ...` starts with "with" and carries no
    semicolon, so it slips past the CLI's string guard undetected. The real
    backstop is that the index is opened via `index_store.connect(...,
    read_only=True)` — a genuine `file:...?mode=ro` URI — so SQLite itself
    refuses the write. Assert the failure is SQLite's read-only error, not the
    guard's rejection message, and that the index file is byte-for-byte
    unchanged: a change that tightened only the string guard (while silently
    dropping `read_only=True`) must not be able to pass this test.
    """
    seeded(project_dir)
    database = project_dir / ".waypoint" / "index.db"
    before = database.read_bytes()
    result = runner.invoke(
        app,
        ["query", "WITH x AS (SELECT 1) DELETE FROM people", "--dir", str(project_dir)],
    )
    assert result.exit_code == 1
    assert "readonly database" in result.stdout
    assert "one SELECT statement" not in result.stdout
    assert database.read_bytes() == before
