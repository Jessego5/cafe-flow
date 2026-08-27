"""The event schema. Written identically by `app/` and `sim/`.

Ground rule 4: the event log is the only source of truth for metrics. It is
append-only, one row per transition, and nothing in `analysis/` may read
mutable state instead.

Ground rule 5: determinism. Event ids are derived from (scenario, seed, seq),
never from a clock or a uuid, so the same seed produces a byte-identical log.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Iterator

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["EventType", "Event", "EventLog", "EVENT_COLUMNS"]


class EventType(StrEnum):
    STATE_CHANGE = "state_change"      # order moved between states
    STATION_START = "station_start"    # a barista seized a station
    STATION_END = "station_end"        # ...and released it
    BATCH_FORMED = "batch_formed"      # items grouped for one station run
    PROMISE_SET = "promise_set"        # a ready-by time was quoted
    ARRIVAL = "arrival"                # a customer appeared (may then balk)


class Event(BaseModel):
    """One immutable row. Every metric in `analysis/` is derived from these."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    event_id: str
    t_s: float                          # seconds since midnight, run clock
    type: EventType
    order_id: str | None = None
    item_id: str | None = None
    customer_id: str | None = None
    from_state: str | None = None
    to_state: str | None = None
    station: str | None = None
    batch_id: str | None = None
    actor: str = "system"
    channel: str | None = None
    is_simulated: bool = False
    scenario: str = ""
    seed: int | None = None
    # which scheduler was in force. A week of logs spanning two policies is
    # uninterpretable without it, and it cannot be recovered afterwards.
    policy: str | None = None
    wall_ts: datetime | None = None     # UTC; None in the simulator
    payload: dict[str, Any] = Field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        """Flat mapping for parquet / SQL / jsonl. Payload stays JSON."""
        data = self.model_dump(mode="json")
        data["payload"] = json.dumps(data["payload"], sort_keys=True)
        return data


EVENT_COLUMNS: tuple[str, ...] = tuple(Event.model_fields)


class EventLog:
    """Append-only sequence of events.

    Holds the run identity so callers never have to repeat it, and assigns the
    monotonic `seq` that makes the log hashable.
    """

    def __init__(
        self,
        scenario: str = "",
        seed: int | None = None,
        *,
        is_simulated: bool = False,
        policy: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.seed = seed
        self.is_simulated = is_simulated
        self.policy = policy
        self._events: list[Event] = []

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __getitem__(self, index: int) -> Event:
        return self._events[index]

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def next_id(self) -> tuple[int, str]:
        seq = len(self._events)
        return seq, f"{self.scenario}:{self.seed}:{seq:07d}"

    def emit(self, type: EventType, t_s: float, **fields: Any) -> Event:
        seq, event_id = self.next_id()
        event = Event(
            seq=seq,
            event_id=event_id,
            t_s=t_s,
            type=type,
            scenario=self.scenario,
            seed=self.seed,
            policy=self.policy,
            is_simulated=self.is_simulated,
            **fields,
        )
        self._events.append(event)
        return event

    def append(self, event: Event) -> Event:
        """Adopt an event built elsewhere (e.g. by `core.states.transition`),
        stamping it with this log's identity and the next sequence number."""
        seq, event_id = self.next_id()
        stamped = event.model_copy(
            update={
                "seq": seq,
                "event_id": event_id,
                "scenario": self.scenario,
                "seed": self.seed,
                "policy": event.policy or self.policy,
                # the log's flag is a floor, not an override: a live log carries
                # simulated orders alongside real ones and must not unflag them.
                "is_simulated": event.is_simulated or self.is_simulated,
            }
        )
        self._events.append(stamped)
        return stamped

    def extend(self, events: Iterable[Event]) -> None:
        for event in events:
            self.append(event)

    def of_type(self, *types: EventType) -> list[Event]:
        wanted = set(types)
        return [e for e in self._events if e.type in wanted]

    def rows(self) -> list[dict[str, Any]]:
        return [event.row() for event in self._events]

    def digest(self) -> str:
        """Content hash. Two runs of the same seed must agree on this."""
        hasher = hashlib.sha256()
        for event in self._events:
            hasher.update(json.dumps(event.row(), sort_keys=True, default=str).encode())
            hasher.update(b"\n")
        return hasher.hexdigest()

    def write_jsonl(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w") as handle:
            for event in self._events:
                handle.write(json.dumps(event.row(), sort_keys=True, default=str) + "\n")
        return target

    def write_parquet(self, path: str | Path) -> Path:
        import pandas as pd  # imported lazily: core has no hard pandas dependency

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.rows(), columns=list(EVENT_COLUMNS)).to_parquet(target, index=False)
        return target

    @classmethod
    def from_rows(
        cls, rows: Iterable[dict[str, Any]], scenario: str = "", seed: int | None = None
    ) -> "EventLog":
        log = cls(scenario=scenario, seed=seed)
        for row in rows:
            data = dict(row)
            payload = data.get("payload")
            if isinstance(payload, str):
                data["payload"] = json.loads(payload) if payload else {}
            log._events.append(Event.model_validate(data))
        return log

    @classmethod
    def read_jsonl(cls, path: str | Path) -> "EventLog":
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        scenario = rows[0].get("scenario", "") if rows else ""
        seed = rows[0].get("seed") if rows else None
        return cls.from_rows(rows, scenario=scenario, seed=seed)
