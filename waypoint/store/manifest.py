"""Per-entity sync state: what arrived, how far, and what went wrong.

The manifest is the single input to panel degradation (UI§6). Its status
vocabulary — ok / partial / failed — is the badge vocabulary on screen, so the
word the user reads is the word in this file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from waypoint.sources.base import EntityStatus

RANK = {"ok": 0, "partial": 1, "failed": 2}
MAX_RUNS = 20


@dataclass
class EntityState:
    key: str
    status: str
    count: int = 0
    watermark: str | None = None
    error: str | None = None
    last_run_at: str | None = None


@dataclass
class RunRecord:
    id: str
    started_at: str
    finished_at: str | None = None
    status: str = "ok"
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class Manifest:
    entities: dict[str, EntityState] = field(default_factory=dict)
    runs: list[RunRecord] = field(default_factory=list)

    def record(
        self, source: str, statuses: dict[str, EntityStatus], run_id: str, at: str
    ) -> None:
        counts: dict[str, int] = {}
        worst = "ok"
        for entity, status in statuses.items():
            key = f"{source}/{entity}"
            self.entities[key] = EntityState(
                key=key,
                status=status.status,
                count=status.count,
                watermark=status.watermark,
                error=status.error,
                last_run_at=at,
            )
            counts[key] = status.count
            if RANK[status.status] > RANK[worst]:
                worst = status.status

        existing = next((run for run in self.runs if run.id == run_id), None)
        if existing is None:
            existing = RunRecord(id=run_id, started_at=at)
            self.runs.append(existing)
            del self.runs[:-MAX_RUNS]
        existing.finished_at = at
        existing.counts.update(counts)
        if RANK[worst] > RANK[existing.status]:
            existing.status = worst

    def status_for(self, keys: Sequence[str]) -> str:
        """Worst status across the entities a panel reads. Unknown = failed."""
        worst = "ok"
        for key in keys:
            state = self.entities.get(key)
            status = state.status if state else "failed"
            if RANK[status] > RANK[worst]:
                worst = status
        return worst

    def watermark(self, key: str) -> str | None:
        state = self.entities.get(key)
        return state.watermark if state else None

    def last_run(self) -> RunRecord | None:
        return self.runs[-1] if self.runs else None

    def digest(self) -> str:
        """Hash of entity state only — run history does not change the data."""
        payload = json.dumps(
            {
                key: {
                    "status": state.status,
                    "count": state.count,
                    "watermark": state.watermark,
                }
                for key, state in sorted(self.entities.items())
            },
            sort_keys=True,
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class ManifestStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "state" / "manifest.json"

    def load(self) -> Manifest:
        if not self.path.exists():
            return Manifest()
        data = json.loads(self.path.read_text())
        return Manifest(
            entities={
                key: EntityState(**state) for key, state in data.get("entities", {}).items()
            },
            runs=[RunRecord(**run) for run in data.get("runs", [])],
        )

    def save(self, manifest: Manifest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entities": {key: asdict(state) for key, state in manifest.entities.items()},
            "runs": [asdict(run) for run in manifest.runs],
            "digest": manifest.digest(),
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
        temporary.replace(self.path)
