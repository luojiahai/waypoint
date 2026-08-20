"""Record and redact real API responses for offline replay in tests.

No test touches the network (§16). Fixtures are captured once, stripped of
tokens, and given synthesized names before they are committed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

SECRET_KEY = re.compile(r"token|authorization|password|secret", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _replace_text(text: str, names: Mapping[str, str]) -> str:
    for real, synthetic in names.items():
        text = text.replace(real, synthetic)
    return EMAIL.sub("person@example.com", text)


def redact(payload, *, names: Mapping[str, str]):
    if isinstance(payload, dict):
        return {
            key: redact(value, names=names)
            for key, value in payload.items()
            if not SECRET_KEY.search(key)
        }
    if isinstance(payload, list):
        return [redact(item, names=names) for item in payload]
    if isinstance(payload, str):
        return _replace_text(payload, names)
    return payload


def capture(project_dir: Path, out_dir: Path, *, limit: int = 5) -> list[Path]:
    """Copy the first `limit` raw records per entity into `out_dir`, redacted."""
    from waypoint.config import load_config
    from waypoint.store.raw import RawStore

    project_dir = Path(project_dir)
    cfg = load_config(project_dir / ".waypoint")
    store = RawStore(project_dir / ".waypoint")
    names = {
        person.github_login: f"user{index}"
        for index, person in enumerate(cfg.people)
        if person.github_login
    }
    names.update(
        {
            person.jira_account_id: f"acct-{index}"
            for index, person in enumerate(cfg.people)
            if person.jira_account_id
        }
    )
    names.update({person.name: f"Person {index}" for index, person in enumerate(cfg.people)})

    written: list[Path] = []
    for source, entity in store.entities():
        target = Path(out_dir) / source / f"{entity}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for record in list(store.read(source, entity))[:limit]:
            lines.append(
                json.dumps(
                    {
                        "source": record.source,
                        "entity": record.entity,
                        "id": _replace_text(record.id, names),
                        "fetched_at": record.fetched_at,
                        "payload": redact(record.payload, names=names),
                    },
                    sort_keys=True,
                )
            )
        target.write_text("\n".join(lines) + "\n")
        written.append(target)
    return written
