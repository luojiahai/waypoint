"""People, and the mapping from source identities to them.

Activity attributed to an identity absent from the roster lands in an explicit
`unattributed` bucket. Dropping it would make the numbers quietly wrong, which
is the one failure mode Waypoint refuses (§4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from waypoint.config import Config

UNATTRIBUTED = "unattributed"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_STRIP.sub("-", name.casefold()).strip("-") or "person"


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    github_login: str
    jira_account_id: str
    active: bool


@dataclass(frozen=True)
class Roster:
    people: tuple[Person, ...]

    @classmethod
    def from_config(cls, cfg: Config) -> Roster:
        people: list[Person] = []
        used: dict[str, int] = {}
        for entry in cfg.people:
            base = _slug(entry.name)
            used[base] = used.get(base, 0) + 1
            person_id = base if used[base] == 1 else f"{base}-{used[base]}"
            people.append(
                Person(
                    id=person_id,
                    name=entry.name,
                    github_login=entry.github_login,
                    jira_account_id=entry.jira_account_id,
                    active=entry.active,
                )
            )
        return cls(people=tuple(people))

    def by_id(self, person_id: str) -> Person | None:
        for person in self.people:
            if person.id == person_id:
                return person
        return None

    def resolve_github(self, login: str | None) -> str:
        if not login:
            return UNATTRIBUTED
        key = login.casefold()
        for person in self.people:
            if person.github_login and person.github_login.casefold() == key:
                return person.id
        return UNATTRIBUTED

    def resolve_jira(self, account_id: str | None) -> str:
        if not account_id:
            return UNATTRIBUTED
        for person in self.people:
            if person.jira_account_id and person.jira_account_id == account_id:
                return person.id
        return UNATTRIBUTED

    def active_people(self) -> tuple[Person, ...]:
        return tuple(sorted((p for p in self.people if p.active), key=lambda p: p.name.casefold()))
