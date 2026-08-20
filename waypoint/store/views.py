"""Last-viewed timestamp per person. A view preference, not data (§7)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from waypoint import clock


class PersonViews:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "state" / "person-views.json"

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def last_viewed(self, person_id: str) -> datetime | None:
        stamp = self._load().get(person_id)
        return clock.parse(stamp) if stamp else None

    def record(self, person_id: str, at: datetime) -> None:
        data = self._load()
        data[person_id] = clock.iso(at)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))
