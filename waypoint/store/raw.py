"""Append-only JSONL snapshots. Never interprets a payload.

Each sync writes a new timestamped file per entity rather than appending to an
existing one, so every sync is a point-in-time snapshot and nothing is ever
mutated or deleted (§7).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from waypoint.sources.base import RawRecord


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "raw"

    def _dir(self, source: str, entity: str) -> Path:
        return self.raw_dir / source / entity

    def write(self, records: Iterable[RawRecord], run_id: str) -> dict[str, int]:
        """Write records into one file per (source, entity). Returns counts."""
        handles: dict[tuple[str, str], object] = {}
        counts: dict[str, int] = {}
        try:
            for record in records:
                key = (record.source, record.entity)
                handle = handles.get(key)
                if handle is None:
                    directory = self._dir(*key)
                    directory.mkdir(parents=True, exist_ok=True)
                    handle = (directory / f"{run_id}.jsonl").open("a", encoding="utf-8")
                    handles[key] = handle
                handle.write(record.to_json() + "\n")
                label = f"{record.source}/{record.entity}"
                counts[label] = counts.get(label, 0) + 1
        finally:
            for handle in handles.values():
                handle.close()
        return counts

    def snapshot_paths(self, source: str, entity: str) -> list[Path]:
        directory = self._dir(source, entity)
        if not directory.exists():
            return []
        return sorted(directory.glob("*.jsonl"))

    def read(self, source: str, entity: str) -> Iterator[RawRecord]:
        """Yield every record for an entity, oldest snapshot first."""
        for path in self.snapshot_paths(source, entity):
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        yield RawRecord.from_json(line)

    def entities(self) -> list[tuple[str, str]]:
        if not self.raw_dir.exists():
            return []
        pairs = [
            (source.name, entity.name)
            for source in sorted(self.raw_dir.iterdir())
            if source.is_dir()
            for entity in sorted(source.iterdir())
            if entity.is_dir()
        ]
        return pairs
