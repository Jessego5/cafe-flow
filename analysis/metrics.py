"""Metrics, computed from the event log and nothing else.

Ground rule 4: the event log is the only source of truth. Nothing here reads
mutable state, and nothing here knows whether the log came from the app or the
simulator — the two write the same schema, so the same functions answer for
both. That is what makes the M9 drift check meaningful.

This module imports `core` for the event schema and the parameters. It must
never import `app/` or `sim/`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from core.events import Event, EventLog, EventType
from core.params import Params
from core.states import State

__all__ = [
    "Interval",
    "load_log",
    "order_waits",
    "wait_percentiles",
    "throughput",
    "peak_throughput",
    "station_busy_seconds",
    "station_utilisation",
    "crew_utilisation",
    "state_counts",
    "busiest_window",
]

SECONDS_PER_HOUR = 3600.0
DEFAULT_PERCENTILES = (50, 90, 95)


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open window of the run clock, in seconds since local midnight."""

    start_s: float
    end_s: float

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s

    def holds(self, t_s: float) -> bool:
        return self.start_s <= t_s < self.end_s

    def overlap_s(self, start_s: float, end_s: float) -> float:
        return max(0.0, min(end_s, self.end_s) - max(start_s, self.start_s))

    @property
    def midpoint_s(self) -> float:
        return self.start_s + self.length_s / 2


def busiest_window(
    log: Iterable[Event] | EventLog,
    *,
    length_s: float = SECONDS_PER_HOUR,
    step_s: float = 300.0,
) -> Interval | None:
    """The window in which the most orders were placed.

    Chosen from the log rather than from the class schedule, so the same
    function finds the rush in a real day as in a simulated one.
    """
    placed = sorted(_reached(_events(log), State.PLACED).values())
    if not placed:
        return None

    times = np.asarray(placed, dtype=float)
    starts = np.arange(times[0], max(times[0], times[-1] - length_s) + step_s, step_s)
    counts = [
        int(((times >= start) & (times < start + length_s)).sum()) for start in starts
    ]
    best = int(np.argmax(counts))
    return Interval(float(starts[best]), float(starts[best] + length_s))


def load_log(path: str | Path) -> EventLog:
    """Read a log written by either runtime."""
    target = Path(path)
    if target.suffix == ".parquet":
        import pandas as pd

        frame = pd.read_parquet(target)
        return EventLog.from_rows(frame.to_dict("records"))
    return EventLog.read_jsonl(target)


def _events(log: Iterable[Event] | EventLog) -> list[Event]:
    return list(log)


def _within(events: Sequence[Event], window: Interval | None) -> list[Event]:
    if window is None:
        return list(events)
    return [event for event in events if window.holds(event.t_s)]


def _reached(events: Sequence[Event], state: State) -> dict[str, float]:
    """First time each order reached a state."""
    out: dict[str, float] = {}
    for event in events:
        if (
            event.type is EventType.STATE_CHANGE
            and event.to_state == state
            and event.order_id is not None
            and event.order_id not in out
        ):
            out[event.order_id] = event.t_s
    return out


def _channels(events: Sequence[Event]) -> dict[str, str]:
    out: dict[str, str] = {}
    for event in events:
        if event.order_id is not None and event.channel and event.order_id not in out:
            out[event.order_id] = event.channel
    return out


# --------------------------------------------------------------------------
# waiting
# --------------------------------------------------------------------------


def order_waits(
    log: Iterable[Event] | EventLog, *, to: State = State.READY
) -> dict[str, float]:
    """Seconds from placing an order to it reaching `to`, per order.

    Orders that never got there are absent rather than zero: a wait that has
    not finished is not a short wait.
    """
    events = _events(log)
    placed = _reached(events, State.PLACED)
    arrived = _reached(events, to)
    return {
        order_id: arrived[order_id] - start
        for order_id, start in placed.items()
        if order_id in arrived
    }


def wait_percentiles(
    log: Iterable[Event] | EventLog,
    *,
    percentiles: Sequence[int] = DEFAULT_PERCENTILES,
    by_channel: bool = False,
    window: Interval | None = None,
    to: State = State.READY,
) -> dict:
    """p50/p90/p95 of the wait, optionally split by channel.

    `window` filters on when the order was *placed*, so a rush is measured by
    the people who joined it rather than by when their drink happened to land.
    """
    events = _events(log)
    placed = _reached(events, State.PLACED)
    waits = order_waits(events, to=to)
    if window is not None:
        waits = {
            order_id: wait
            for order_id, wait in waits.items()
            if window.holds(placed[order_id])
        }

    if not by_channel:
        return _percentiles(list(waits.values()), percentiles)

    channels = _channels(events)
    grouped: dict[str, list[float]] = defaultdict(list)
    for order_id, wait in waits.items():
        grouped[channels.get(order_id, "unknown")].append(wait)
    return {channel: _percentiles(values, percentiles) for channel, values in grouped.items()}


def _percentiles(values: Sequence[float], percentiles: Sequence[int]) -> dict:
    if not values:
        return {"n": 0, **{f"p{p}": None for p in percentiles}, "mean": None, "max": None}
    array = np.asarray(values, dtype=float)
    out: dict = {"n": int(array.size)}
    for percentile in percentiles:
        out[f"p{percentile}"] = float(np.percentile(array, percentile))
    out["mean"] = float(array.mean())
    out["max"] = float(array.max())
    return out


# --------------------------------------------------------------------------
# throughput
# --------------------------------------------------------------------------


def throughput(
    log: Iterable[Event] | EventLog,
    *,
    window_s: float = SECONDS_PER_HOUR,
    state: State = State.READY,
) -> list[tuple[float, float]]:
    """Completions per hour, in fixed bins. Returns (bin start, rate) pairs."""
    events = _events(log)
    completed = sorted(_reached(events, state).values())
    if not completed:
        return []

    first = np.floor(completed[0] / window_s) * window_s
    last = np.floor(completed[-1] / window_s) * window_s
    edges = np.arange(first, last + window_s, window_s)
    counts, _ = np.histogram(completed, bins=np.append(edges, edges[-1] + window_s))
    scale = SECONDS_PER_HOUR / window_s
    return [(float(edge), float(count * scale)) for edge, count in zip(edges, counts)]


def peak_throughput(
    log: Iterable[Event] | EventLog,
    *,
    window_s: float = SECONDS_PER_HOUR,
    state: State = State.READY,
) -> float:
    series = throughput(log, window_s=window_s, state=state)
    return max((rate for _, rate in series), default=0.0)


# --------------------------------------------------------------------------
# stations and crew
# --------------------------------------------------------------------------


def _spans(events: Sequence[Event]) -> list[tuple[str | None, str, float, float]]:
    """(station, actor, start, end) for every completed piece of station work."""
    open_at: dict[tuple, list[float]] = defaultdict(list)
    spans: list[tuple[str | None, str, float, float]] = []
    for event in events:
        if event.type not in (EventType.STATION_START, EventType.STATION_END):
            continue
        key = (event.actor, event.station, event.item_id, event.order_id)
        if event.type is EventType.STATION_START:
            open_at[key].append(event.t_s)
        elif open_at[key]:
            spans.append((event.station, event.actor, open_at[key].pop(), event.t_s))
    return spans


def station_busy_seconds(
    log: Iterable[Event] | EventLog, *, window: Interval | None = None
) -> dict[str, float]:
    """Seconds of work done at each station. Assembly work has no station and
    is reported under `None`."""
    busy: dict[str, float] = defaultdict(float)
    for station, _actor, start, end in _spans(_events(log)):
        busy[station] += (end - start) if window is None else window.overlap_s(start, end)
    return dict(busy)


def station_utilisation(
    log: Iterable[Event] | EventLog,
    params: Params,
    *,
    window: Interval | None = None,
) -> dict[str, float]:
    """Busy fraction per station, against its parallel capacity.

    Capacity is resolved at the middle of the window, because a station whose
    capacity follows the staffing plan has a different ceiling at 08:00 than at
    11:00.
    """
    if window is None:
        window = Interval(float(params.meta.start_s), float(params.meta.end_s))

    busy = station_busy_seconds(log, window=window)
    out: dict[str, float] = {}
    for station, seconds in busy.items():
        if station is None:
            continue
        capacity = params.station_capacity(station, window.midpoint_s)
        out[station] = seconds / (window.length_s * capacity)
    return out


def crew_utilisation(
    log: Iterable[Event] | EventLog,
    params: Params,
    *,
    window: Interval | None = None,
) -> float:
    """Busy fraction of the whole crew.

    A barista holds one piece of work at a time, so their spans never overlap
    and this is simply their summed work over their available time.
    """
    if window is None:
        window = Interval(float(params.meta.start_s), float(params.meta.end_s))

    busy = sum(
        window.overlap_s(start, end) for _station, _actor, start, end in _spans(_events(log))
    )
    baristas = params.baristas_at(window.midpoint_s)
    return busy / (window.length_s * baristas)


def state_counts(log: Iterable[Event] | EventLog) -> dict[str, int]:
    """How many orders ended in each state, plus how many never finished."""
    events = _events(log)
    final: dict[str, str] = {}
    for event in events:
        if event.type is EventType.STATE_CHANGE and event.order_id is not None:
            final[event.order_id] = event.to_state

    counts: dict[str, int] = defaultdict(int)
    for state in final.values():
        counts[state] += 1
    counts["placed"] = len(final)
    counts["in_flight"] = sum(
        1 for state in final.values() if not State.is_terminal(State(state))
    )
    return dict(counts)
