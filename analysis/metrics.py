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
    "Span",
    "machine_utilisation",
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
    "balk_count_and_lost_margin",
    "promises",
    "experienced_waits",
    "promise_error",
    "batch_rate",
    "fairness_gap",
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


@dataclass(frozen=True, slots=True)
class Span:
    """One completed piece of station work.

    `attended` separates the two questions the log has to answer: a press cycle
    occupies the press for four minutes but a person for none of it, so machine
    utilisation and crew utilisation are not the same number.
    """

    station: str | None
    actor: str
    start_s: float
    end_s: float
    attended: bool = True

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s


def _spans(events: Sequence[Event]) -> list[Span]:
    open_at: dict[tuple, list[Event]] = defaultdict(list)
    spans: list[Span] = []
    for event in events:
        if event.type not in (EventType.STATION_START, EventType.STATION_END):
            continue
        key = (event.actor, event.station, event.item_id, event.order_id)
        if event.type is EventType.STATION_START:
            open_at[key].append(event)
        elif open_at[key]:
            started = open_at[key].pop()
            spans.append(
                Span(
                    station=event.station,
                    actor=event.actor,
                    start_s=started.t_s,
                    end_s=event.t_s,
                    attended=bool(started.payload.get("attended", True)),
                )
            )
    return spans


def station_busy_seconds(
    log: Iterable[Event] | EventLog, *, window: Interval | None = None
) -> dict[str, float]:
    """Seconds of work done at each station. Assembly work has no station and
    is reported under `None`."""
    busy: dict[str, float] = defaultdict(float)
    for span in _spans(_events(log)):
        busy[span.station] += (
            span.length_s if window is None else window.overlap_s(span.start_s, span.end_s)
        )
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


def machine_utilisation(
    log: Iterable[Event] | EventLog,
    params: Params,
    *,
    window: Interval | None = None,
) -> dict[str, float]:
    """Alias for `station_utilisation`, named for what it measures once
    attended and unattended work are told apart."""
    return station_utilisation(log, params, window=window)


def crew_utilisation(
    log: Iterable[Event] | EventLog,
    params: Params,
    *,
    window: Interval | None = None,
) -> float:
    """Busy fraction of the whole crew.

    Counts attended work only. A press running on its own is not a person being
    busy, and treating it as one is what makes a shift look fuller than it is.
    """
    if window is None:
        window = Interval(float(params.meta.start_s), float(params.meta.end_s))

    busy = sum(
        window.overlap_s(span.start_s, span.end_s)
        for span in _spans(_events(log))
        if span.attended
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


def balk_count_and_lost_margin(
    log: Iterable[Event] | EventLog,
    params: Params | None = None,
    *,
    window: Interval | None = None,
) -> dict:
    """What the queue cost, in customers and in money.

    Read from the log alone: the margin behind an order is written onto the
    event when it is placed and again when it is lost, so this needs no access
    to the menu and works the same on a real day as on a simulated one. `params`
    is accepted for the signature the plan fixes and is not required.

    A balk is someone who looked at the line and left. An abandonment is someone
    who ordered, waited, and was gone by the time it was ready — the cafe made
    that one, so it cost ingredients as well as the sale.
    """
    events = _within(_events(log), window)

    placed = 0
    offered_cents = 0
    lost: dict[str, int] = defaultdict(int)
    lost_cents: dict[str, int] = defaultdict(int)

    for event in events:
        if event.type is not EventType.STATE_CHANGE:
            continue
        if event.to_state == State.PLACED:
            placed += 1
            offered_cents += int(event.payload.get("margin_cents", 0))
        elif event.to_state in (State.BALKED, State.ABANDONED):
            reason = event.payload.get("reason") or str(event.to_state)
            lost[reason] += 1
            lost_cents[reason] += int(event.payload.get("margin_cents", 0))

    total_lost = sum(lost.values())
    total_lost_cents = sum(lost_cents.values())
    return {
        "placed": placed,
        "balked": lost.get(str(State.BALKED), 0),
        "abandoned": total_lost - lost.get(str(State.BALKED), 0),
        "lost": total_lost,
        "lost_fraction": total_lost / placed if placed else 0.0,
        "by_reason": dict(lost),
        "lost_margin_cents": total_lost_cents,
        "offered_margin_cents": offered_cents,
        "captured_margin_cents": offered_cents - total_lost_cents,
    }


def promises(log: Iterable[Event] | EventLog) -> dict[str, float]:
    """The ready-by time quoted for each order, as it was quoted at the time."""
    return {
        event.order_id: float(event.payload["promised_at_s"])
        for event in _events(log)
        if event.type is EventType.PROMISE_SET
        and event.order_id is not None
        and "promised_at_s" in event.payload
    }


def experienced_waits(
    log: Iterable[Event] | EventLog, *, to: State = State.READY
) -> dict[str, float]:
    """How long each customer actually stood there.

    Not the same as placed-to-ready. Someone who ordered ahead for eleven
    o'clock and collected at eleven waited no time at all, and counting their
    lead time as waiting would make ordering ahead look like the worst thing
    the cafe offers. Their clock starts at the time they were promised.
    """
    events = _events(log)
    placed = _reached(events, State.PLACED)
    arrived = _reached(events, to)
    quoted = promises(events)

    waits: dict[str, float] = {}
    for order_id, ready_at in arrived.items():
        reference = quoted.get(order_id, placed.get(order_id))
        if reference is None:
            continue
        waits[order_id] = max(0.0, ready_at - reference)
    return waits


def promise_error(
    log: Iterable[Event] | EventLog,
    *,
    percentiles: Sequence[int] = DEFAULT_PERCENTILES,
    window: Interval | None = None,
) -> dict:
    """Ready-at minus promised-at, signed, in seconds.

    Negative is early. `late_fraction` is the number that matters to a
    customer: a promise kept on average but missed a third of the time is not
    a promise anyone will rely on twice.
    """
    events = _within(_events(log), window)
    quoted = promises(events)
    ready = _reached(events, State.READY)

    errors = [ready[order_id] - at for order_id, at in quoted.items() if order_id in ready]
    stats = _percentiles(errors, percentiles)
    stats["late"] = sum(1 for error in errors if error > 0)
    stats["late_fraction"] = stats["late"] / len(errors) if errors else 0.0
    stats["promised"] = len(quoted)
    return stats


def batch_rate(
    log: Iterable[Event] | EventLog, *, window: Interval | None = None
) -> dict:
    """How much of the batchable work was actually made in company.

    Reads the size recorded on each station run, so a station that ran one item
    when it could have held two counts against the rate. Per station, because
    the aggregate hides the thing worth knowing: on this menu the press batches
    and the wand barely does.
    """
    events = _within(_events(log), window)

    items: dict[str, int] = defaultdict(int)
    grouped: dict[str, int] = defaultdict(int)
    runs: dict[str, int] = defaultdict(int)

    for event in events:
        if event.type is not EventType.STATION_START or event.station is None:
            continue
        size = int(event.payload.get("size", 0) or 0)
        if not size:
            continue
        items[event.station] += size
        runs[event.station] += 1
        if size > 1:
            grouped[event.station] += size

    by_station = {
        station: {
            "items": count,
            "runs": runs[station],
            "in_a_batch": grouped[station],
            "rate": grouped[station] / count if count else 0.0,
            "items_per_run": count / runs[station] if runs[station] else 0.0,
            "runs_saved": count - runs[station],
        }
        for station, count in items.items()
    }

    total_items = sum(items.values())
    total_grouped = sum(grouped.values())
    return {
        "by_station": by_station,
        "items": total_items,
        "in_a_batch": total_grouped,
        "rate": total_grouped / total_items if total_items else 0.0,
        "runs_saved": total_items - sum(runs.values()),
    }


def fairness_gap(
    log: Iterable[Event] | EventLog,
    *,
    percentile: int = 95,
    window: Interval | None = None,
) -> dict:
    """How much worse the queue is for the people standing in it.

    Measured on what each customer actually experienced, so the pre-order lead
    time does not count against them. A large positive gap means ordering ahead
    is buying its adopters a materially better cafe than everyone else gets,
    which is a policy question rather than a bug — but one nobody can weigh
    without the number.
    """
    events = _events(log)
    placed = _reached(events, State.PLACED)
    channels = _channels(events)
    waits = experienced_waits(events)

    grouped: dict[str, list[float]] = defaultdict(list)
    for order_id, wait in waits.items():
        if window is not None and not window.holds(placed.get(order_id, -1)):
            continue
        grouped[channels.get(order_id, "unknown")].append(wait)

    stats = {
        channel: _percentiles(values, (percentile,))
        for channel, values in grouped.items()
    }
    walkup = stats.get("walkup", {}).get(f"p{percentile}")
    preorder = stats.get("preorder", {}).get(f"p{percentile}")
    return {
        "percentile": percentile,
        "by_channel": stats,
        "walkup_s": walkup,
        "preorder_s": preorder,
        "gap_s": None if walkup is None or preorder is None else walkup - preorder,
    }
