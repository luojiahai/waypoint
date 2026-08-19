"""Exceptions that carry a message a user can act on.

Every message names what failed and what to do about it. A stack trace is a
development artefact; the user gets `.message`.
"""


class WaypointError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigError(WaypointError):
    """Configuration is missing, malformed, or internally inconsistent."""


class SourceError(WaypointError):
    """A connector could not complete a fetch."""

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


class BuildError(WaypointError):
    """The index could not be built from raw data."""
