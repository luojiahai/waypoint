"""Every coordinate in every chart is computed here.

Arithmetic in a template is a defect (§6): the template interpolates a finished
`points` string and does no scaling of its own. There is no charting library —
inline SVG is less code than configuring one and keeps the app fully offline
(§12, UI§4).

No function here takes a goal line. The only reference marks in Waypoint are the
WIP-limit tick and the aging threshold, both of which flag an item for attention
rather than setting a number to reach (§10).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

FILL_OK = "#3b6b4f"
FILL_HIGH = "#5a2f2f"
FILL_MED = "#5c4520"
FILL_NEUTRAL = "#2a3038"
DOT_UNDER = "#4a5260"
STROKE_OK = "#7dd3a0"
STROKE_P75 = "#4a7a5e"
TICK = "#8b93a1"
TEXT_3 = "#6b7280"
MED = "#e0b060"
HIGH = "#e06c6c"


@dataclass(frozen=True)
class WipBar:
    name: str
    count: int
    limit: int | None
    label: str
    track_width: float
    track_height: float
    svg_height: float
    fill_width: float
    fill_color: str
    tick_x: float | None
    over: bool
    no_limit: bool


def wip_bar(name: str, count: int, limit: int | None, *, width: int = 180, height: int = 16) -> WipBar:
    """A count against its column's WIP limit. No limit set is not a limit of zero."""
    if not limit:
        return WipBar(
            name=name, count=count, limit=None, label=f"{count} · no limit",
            track_width=float(width), track_height=float(height), svg_height=float(height) + 4,
            fill_width=0.0 if count == 0 else float(width),
            fill_color=TEXT_3, tick_x=None, over=False, no_limit=True,
        )
    ratio = count / limit
    return WipBar(
        name=name, count=count, limit=limit, label=f"{count} / {limit}",
        track_width=float(width), track_height=float(height), svg_height=float(height) + 4,
        fill_width=round(min(1.0, ratio) * width, 2),
        fill_color=FILL_MED if count >= limit else FILL_OK,
        tick_x=float(width), over=count > limit, no_limit=False,
    )


@dataclass(frozen=True)
class Spark:
    width: int
    height: int
    points: str
    p75_points: str
    has_data: bool


def _polyline(values: Sequence[float | None], width: int, height: int,
              low: float, high: float) -> str:
    present = [(index, value) for index, value in enumerate(values) if value is not None]
    if not present:
        return ""
    span = max(1, len(values) - 1)
    scale = (high - low) or 1.0
    points = []
    for index, value in present:
        x = round(index / span * width, 2)
        y = round(height - ((value - low) / scale) * height, 2)
        points.append(f"{x},{y}")
    return " ".join(points)


def sparkline(
    median: Sequence[float | None],
    p75: Sequence[float | None] = (),
    *,
    width: int = 200,
    height: int = 42,
) -> Spark:
    real = [v for v in list(median) + list(p75) if v is not None]
    if not real:
        return Spark(width=width, height=height, points="", p75_points="", has_data=False)
    low, high = min(real), max(real)
    if low == high:
        low, high = low - 1, high + 1
    return Spark(
        width=width,
        height=height,
        points=_polyline(median, width, height, low, high),
        p75_points=_polyline(p75, width, height, low, high),
        has_data=True,
    )


@dataclass(frozen=True)
class Bar:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class BarSpark:
    width: int
    height: int
    bars: list[Bar] = field(default_factory=list)
    has_data: bool = False


def bar_spark(values: Sequence[int], *, width: int = 200, height: int = 42) -> BarSpark:
    """A count per week is discrete, so throughput uses bars rather than a line."""
    values = list(values)
    if not values:
        return BarSpark(width=width, height=height, bars=[], has_data=False)
    peak = max(values) or 1
    slot = width / len(values)
    bar_width = round(slot * 0.7, 2)
    bars = []
    for index, value in enumerate(values):
        bar_height = round(value / peak * height, 2)
        bars.append(
            Bar(
                x=round(index * slot, 2),
                y=round(height - bar_height, 2),
                width=bar_width,
                height=bar_height,
            )
        )
    return BarSpark(width=width, height=height, bars=bars, has_data=True)


@dataclass(frozen=True)
class Progress:
    width: int
    height: int
    fill_width: float
    fill_color: str


def progress_bar(done: float, total: float, *, width: int = 120, height: int = 5) -> Progress:
    ratio = 0.0 if not total else min(1.0, max(0.0, done / total))
    return Progress(width=width, height=height, fill_width=round(ratio * width, 2), fill_color=FILL_OK)


@dataclass(frozen=True)
class AgingItem:
    key: str
    age_days: float


@dataclass(frozen=True)
class AgingLaneInput:
    label: str
    sublabel: str
    items: Sequence[AgingItem]


@dataclass(frozen=True)
class AgingDot:
    cx: float
    cy: float
    color: str
    key: str
    age_days: float


@dataclass(frozen=True)
class AgingLane:
    label: str
    sublabel: str
    y: float
    dots: list[AgingDot]


@dataclass(frozen=True)
class AgingChart:
    width: int
    height: float
    lanes: list[AgingLane]
    threshold_x: float
    threshold_days: int
    x_ticks: list[tuple[float, str]]
    oldest_label: str | None
    oldest_x: float | None
    oldest_y: float | None
    has_data: bool


def _dot_color(age_days: float, threshold_days: int) -> str:
    if age_days >= threshold_days * 2:
        return HIGH
    if age_days >= threshold_days:
        return MED
    return DOT_UNDER


def aging_chart(
    lanes: Sequence[AgingLaneInput], threshold_days: int, *, width: int = 520, lane_height: int = 34
) -> AgingChart:
    """One lane per WIP column against a shared horizontal age axis in days."""
    ages = [item.age_days for lane in lanes for item in lane.items]
    axis_max = max([*ages, threshold_days * 1.5, 1.0])
    scale = width / axis_max

    built: list[AgingLane] = []
    oldest: tuple[float, float, AgingItem] | None = None
    for index, lane in enumerate(lanes):
        y = round((index + 0.5) * lane_height, 2)
        dots = []
        for item in lane.items:
            cx = round(item.age_days * scale, 2)
            dots.append(
                AgingDot(
                    cx=cx, cy=y, color=_dot_color(item.age_days, threshold_days),
                    key=item.key, age_days=item.age_days,
                )
            )
            if oldest is None or item.age_days > oldest[2].age_days:
                oldest = (cx, y, item)
        built.append(AgingLane(label=lane.label, sublabel=lane.sublabel, y=y, dots=dots))

    ticks = [
        (round(fraction * width, 2), f"{round(fraction * axis_max)}d")
        for fraction in (0.0, 0.33, 0.66, 1.0)
    ]
    return AgingChart(
        width=width,
        height=round(len(lanes) * lane_height, 2),
        lanes=built,
        threshold_x=round(threshold_days * scale, 2),
        threshold_days=threshold_days,
        x_ticks=ticks,
        oldest_label=None if oldest is None else f"{oldest[2].key} · {round(oldest[2].age_days)}d",
        oldest_x=None if oldest is None else oldest[0],
        oldest_y=None if oldest is None else oldest[1],
        has_data=bool(ages),
    )
