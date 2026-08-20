import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from waypoint.cli import app as cli_app
from waypoint.web.app import create_app

runner = CliRunner()

WEB_DIR = Path(__file__).parent.parent / "waypoint" / "web"


@pytest.fixture
def client(project_dir: Path) -> TestClient:
    return TestClient(create_app(project_dir))


def test_htmx_is_vendored_not_linked_from_a_cdn():
    static = WEB_DIR / "static" / "htmx.min.js"
    assert static.exists()
    assert static.stat().st_size > 10_000


def test_no_template_references_an_external_host():
    for path in (WEB_DIR / "templates").rglob("*.html"):
        text = path.read_text()
        assert "http://" not in text
        assert "https://cdn" not in text
        assert "fonts.googleapis" not in text


def test_stylesheet_defines_every_palette_token():
    css = (WEB_DIR / "static" / "waypoint.css").read_text()
    for token, value in [
        ("--ground", "#0f1216"), ("--panel", "#12151a"), ("--border", "#232830"),
        ("--border-dim", "#1c2028"), ("--text", "#e6eaf0"), ("--text-2", "#8b93a1"),
        ("--text-3", "#6b7280"), ("--text-4", "#5a6270"), ("--ok", "#7dd3a0"),
        ("--high", "#e06c6c"), ("--med", "#e0b060"), ("--stale", "#6ba3d6"),
        ("--skill", "#a98fd6"),
    ]:
        assert f"{token}: {value}" in css


def test_only_the_wordmark_uses_weight_600():
    css = (WEB_DIR / "static" / "waypoint.css").read_text()
    blocks = re.findall(r"([^{}]+)\{([^}]*)\}", css)
    heavy = [selector.strip() for selector, body in blocks if "font-weight: 600" in body]
    assert heavy == [".wordmark"]


def test_the_font_stack_is_monospace_everywhere():
    css = (WEB_DIR / "static" / "waypoint.css").read_text()
    assert "ui-monospace, SFMono-Regular, Menlo, monospace" in css
    assert "sans-serif" not in css


def test_chrome_carries_the_wordmark_four_nav_items_and_the_sync_button(client: TestClient):
    body = client.get("/").text
    assert 'class="wordmark"' in body
    for label in ("home", "delivery", "people", "sync"):
        assert f'href="/{"" if label == "home" else label}"' in body
    assert "Sync" in body


def test_a_fresh_install_shows_the_first_run_panel_on_every_page(client: TestClient):
    for path in ("/", "/delivery", "/people", "/sync"):
        body = client.get(path).text
        assert "No data yet" in body
        assert "waypoint doctor" in body


def test_the_active_nav_item_is_marked(client: TestClient):
    body = client.get("/delivery").text
    assert 'class="nav-item active"' in body


def test_sync_state_reads_as_never_synced_before_the_first_run(client: TestClient):
    assert "never synced" in client.get("/").text


def test_sync_state_reflects_a_partial_run(project_dir: Path):
    from waypoint.sources.base import EntityStatus
    from waypoint.store.manifest import ManifestStore

    store = ManifestStore(project_dir / ".waypoint")
    manifest = store.load()
    manifest.record("github", {"pull_requests": EntityStatus("pull_requests", "partial", 3)},
                    "r1", "2026-08-19T09:12:03Z")
    store.save(manifest)
    body = TestClient(create_app(project_dir)).get("/").text
    assert "last sync partial" in body


def test_static_assets_are_served(client: TestClient):
    assert client.get("/static/waypoint.css").status_code == 200
    assert client.get("/static/htmx.min.js").status_code == 200


def test_serve_command_exists():
    result = runner.invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "8787" in result.stdout
