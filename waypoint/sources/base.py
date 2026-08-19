"""What every connector emits, and the contract it satisfies.

A connector produces raw payloads and per-entity status. It interprets nothing:
metric logic downstream can be revised and the index rebuilt without a re-fetch,
which only holds if nothing upstream has already thrown information away.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol

OK = "ok"
PARTIAL = "partial"
FAILED = "failed"


@dataclass(frozen=True)
class RawRecord:
    source: str
    entity: str
    id: str
    fetched_at: str
    payload: dict

    def to_json(self) -> str:
        return json.dumps(
            {
                "source": self.source,
                "entity": self.entity,
                "id": self.id,
                "fetched_at": self.fetched_at,
                "payload": self.payload,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, line: str) -> RawRecord:
        data = json.loads(line)
        return cls(
            source=data["source"],
            entity=data["entity"],
            id=data["id"],
            fetched_at=data["fetched_at"],
            payload=data["payload"],
        )


@dataclass
class EntityStatus:
    entity: str
    status: str = OK
    count: int = 0
    error: str | None = None
    watermark: str | None = None


class Source(Protocol):
    """Fetch raw records; report per-entity status afterwards.

    `fetch` must not raise for a per-entity failure. It records the failure in
    `status()` and moves to the next entity, so a Jira outage never discards
    what GitHub already returned (§15).
    """

    name: str
    entities: tuple[str, ...]

    def fetch(self, since: Mapping[str, str | None]) -> Iterator[RawRecord]: ...

    def status(self) -> dict[str, EntityStatus]: ...
