"""Skill reports: a markdown file for the user and a JSON sidecar for the UI.

The UI never parses prose (§13) — if it did, a rephrased sentence would break a
panel. The schema also enforces grounding structurally: an item with an empty
`evidence` array is invalid and is dropped on render.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from waypoint import clock

SEVERITIES = {"high", "med", "low"}
TOP_LEVEL = ("skill", "generated_at", "window", "inputs_digest", "items")


class SidecarError(Exception):
    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def validate_sidecar(data: object) -> None:
    """Raise SidecarError unless `data` matches the sidecar schema in §13."""
    if not isinstance(data, dict):
        raise SidecarError("Sidecar must be a JSON object.", "")
    for key in TOP_LEVEL:
        if key not in data:
            raise SidecarError(f"Sidecar is missing `{key}`.", "")
    window = data["window"]
    if not isinstance(window, dict) or "from" not in window or "to" not in window:
        raise SidecarError("`window` must be an object with `from` and `to`.", "window")
    if not isinstance(data["items"], list):
        raise SidecarError("`items` must be a list.", "items")
    for index, item in enumerate(data["items"]):
        where = f"items/{index}"
        if not isinstance(item, dict):
            raise SidecarError("Each item must be an object.", where)
        for key in ("severity", "title", "body", "evidence"):
            if key not in item:
                raise SidecarError(f"Item is missing `{key}`.", where)
        if item["severity"] not in SEVERITIES:
            raise SidecarError(
                f"Unknown `severity` {item['severity']!r}; expected one of "
                f"{sorted(SEVERITIES)}.",
                where,
            )
        if not isinstance(item["evidence"], list):
            raise SidecarError("`evidence` must be a list.", f"{where}/evidence")
        for position, source in enumerate(item["evidence"]):
            if not isinstance(source, dict) or "type" not in source or "ref" not in source:
                raise SidecarError(
                    "Each evidence entry needs `type` and `ref`.",
                    f"{where}/evidence/{position}",
                )


@dataclass(frozen=True)
class ReportItem:
    severity: str
    title: str
    body: str
    evidence: list[dict]
    question: str | None = None


@dataclass(frozen=True)
class Report:
    skill: str
    generated_at: str
    window_from: str
    window_to: str
    inputs_digest: str
    items: list[ReportItem] = field(default_factory=list)
    sidecar_path: Path | None = None
    markdown_path: Path | None = None
    malformed: bool = False
    error: str | None = None


def _slug(skill: str) -> str:
    return skill.split(":", 1)[-1]


def _stem(skill: str, person_id: str | None) -> str:
    """`delivery-risk`, or `one-on-one-prep-alex-rivera` for a person-scoped skill."""
    slug = _slug(skill)
    return f"{slug}-{person_id}" if person_id else slug


class ReportStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "reports"

    def write(
        self, skill: str, sidecar: dict, markdown: str, *, at: datetime,
        person_id: str | None = None,
    ) -> Report:
        validate_sidecar(sidecar)
        self.dir.mkdir(parents=True, exist_ok=True)
        stem = f"{clock.iso(at)[:10]}-{_stem(skill, person_id)}"
        (self.dir / f"{stem}.md").write_text(markdown)
        (self.dir / f"{stem}.json").write_text(json.dumps(sidecar, indent=2))
        return self._load(self.dir / f"{stem}.json")

    def _load(self, sidecar_path: Path) -> Report:
        markdown_path = sidecar_path.with_suffix(".md")
        try:
            data = json.loads(sidecar_path.read_text())
            validate_sidecar(data)
        except (json.JSONDecodeError, SidecarError, UnicodeDecodeError) as exc:
            # `skill` is left blank rather than reconstructed from the filename
            # stem: a malformed sidecar's own content is untrusted, and welding
            # a person id onto a guessed slug would claim a skill id that no
            # skill actually has. `sidecar_path` still carries the raw stem for
            # anyone who needs it.
            return Report(
                skill="",
                generated_at="",
                window_from="",
                window_to="",
                inputs_digest="",
                sidecar_path=sidecar_path,
                markdown_path=markdown_path if markdown_path.exists() else None,
                malformed=True,
                error=getattr(exc, "message", str(exc)),
            )
        items = [
            ReportItem(
                severity=item["severity"],
                title=item["title"],
                body=item["body"],
                evidence=item["evidence"],
                question=item.get("question"),
            )
            for item in data["items"]
            if item["evidence"]  # grounding rule: no evidence, no render (§13)
        ]
        return Report(
            skill=data["skill"],
            generated_at=data["generated_at"],
            window_from=data["window"]["from"],
            window_to=data["window"]["to"],
            inputs_digest=data["inputs_digest"],
            items=items,
            sidecar_path=sidecar_path,
            markdown_path=markdown_path if markdown_path.exists() else None,
        )

    def all_reports(self) -> list[Report]:
        if not self.dir.exists():
            return []
        return [self._load(path) for path in sorted(self.dir.glob("*.json"))]

    def latest(self, skill: str, person_id: str | None = None) -> Report | None:
        pattern = f"*-{_stem(skill, person_id)}.json"
        matches = sorted(self.dir.glob(pattern)) if self.dir.exists() else []
        return self._load(matches[-1]) if matches else None
