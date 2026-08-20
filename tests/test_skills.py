import json
import re
from pathlib import Path

import pytest

from waypoint import skills_runner
from waypoint.store.reports import validate_sidecar

SKILLS_DIR = Path(__file__).parent.parent / "skills"
PEOPLE_SKILLS = ("one-on-one-prep", "growth-review")


def skill_dirs():
    return sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir())


def test_one_directory_per_registered_skill():
    assert [path.name for path in skill_dirs()] == sorted(skills_runner.SKILLS)


@pytest.mark.parametrize("slug", sorted(skills_runner.SKILLS))
def test_frontmatter_names_the_skill_correctly(slug: str):
    text = (SKILLS_DIR / slug / "SKILL.md").read_text()
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    assert f"name: waypoint:{slug}" in frontmatter
    assert "description:" in frontmatter


@pytest.mark.parametrize("slug", sorted(skills_runner.SKILLS))
def test_each_skill_reads_data_through_waypoint_query(slug: str):
    text = (SKILLS_DIR / slug / "SKILL.md").read_text()
    assert "waypoint query" in text
    assert "sqlite3 " not in text


@pytest.mark.parametrize("slug", sorted(skills_runner.SKILLS))
def test_each_skill_states_the_grounding_rule(slug: str):
    text = (SKILLS_DIR / slug / "SKILL.md").read_text().lower()
    assert "insufficient data" in text
    assert "evidence" in text


@pytest.mark.parametrize("slug", sorted(skills_runner.SKILLS))
def test_each_skill_writes_both_files_to_the_reports_directory(slug: str):
    text = (SKILLS_DIR / slug / "SKILL.md").read_text()
    assert ".waypoint/reports/" in text
    assert ".md" in text and ".json" in text


@pytest.mark.parametrize("slug", PEOPLE_SKILLS)
def test_people_skills_inherit_the_principle_explicitly(slug: str):
    text = (SKILLS_DIR / slug / "SKILL.md").read_text().lower()
    assert "never compare people" in text
    assert "questions, never assessments" in text
    assert "never characterize performance" in text


@pytest.mark.parametrize("slug", sorted(skills_runner.SKILLS))
def test_each_example_output_passes_the_validator(slug: str):
    data = json.loads((SKILLS_DIR / slug / "example-output.json").read_text())
    validate_sidecar(data)


@pytest.mark.parametrize("slug", sorted(skills_runner.SKILLS))
def test_each_example_item_carries_evidence(slug: str):
    data = json.loads((SKILLS_DIR / slug / "example-output.json").read_text())
    assert data["items"]
    for item in data["items"]:
        assert item["evidence"]


@pytest.mark.parametrize("slug", PEOPLE_SKILLS)
def test_people_skill_examples_emit_questions(slug: str):
    data = json.loads((SKILLS_DIR / slug / "example-output.json").read_text())
    for item in data["items"]:
        assert item.get("question")


def test_no_skill_asks_for_sprints():
    for path in SKILLS_DIR.rglob("SKILL.md"):
        assert "sprint" not in path.read_text().lower()


ISSUE_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
PR_REF_RE = re.compile(r"PR #\d+")


@pytest.mark.parametrize("slug", sorted(skills_runner.SKILLS))
def test_example_item_evidence_covers_every_reference_named_in_prose(slug: str):
    """The example is the reference document a model imitates: an issue or PR
    named in an item's title/body without a matching evidence entry teaches
    exactly the unevidenced-claim behaviour the grounding rule forbids."""
    data = json.loads((SKILLS_DIR / slug / "example-output.json").read_text())
    for item in data["items"]:
        text = f"{item['title']} {item['body']}"
        cited = set(ISSUE_KEY_RE.findall(text)) | set(PR_REF_RE.findall(text))
        evidenced = {source["ref"] for source in item["evidence"]}
        missing = cited - evidenced
        assert not missing, f"{slug}: cited in prose but not evidenced: {missing}"
