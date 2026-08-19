"""The only source of wall-clock time in Waypoint.

Metric functions never call `now()`; they take the current time as an argument.
Everything else calls `now()` here so tests can substitute a fixed instant.
"""

from datetime import UTC, datetime

_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """Render as ISO-8601 UTC with a Z suffix and whole seconds."""
    return dt.astimezone(UTC).strftime(_FORMAT)


def parse(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Accepts a Z suffix, a numeric offset, fractional seconds, or no zone at all
    (assumed UTC). Raises ValueError on anything else.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def run_id(dt: datetime) -> str:
    """A sync identifier usable as a filename on every platform."""
    return iso(dt).replace(":", "-")
