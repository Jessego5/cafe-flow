"""Domain objects shared by the app and the simulator.

Plain dataclasses on purpose: `app/` persists them through SQLModel tables and
`sim/` moves them through SimPy processes, and neither may add rules of its own
(ground rule 2). Anything that decides *what a drink costs* or *what may happen
next* lives in `core/`, not here in the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from core.events import Event
from core.states import State

__all__ = ["Channel", "TaskKind", "Line", "Task", "Item", "Order", "Batch", "Customer"]


class Channel(StrEnum):
    WALKUP = "walkup"
    PREORDER = "preorder"


class TaskKind(StrEnum):
    PREP = "prep"          # occupies a station
    ASSEMBLY = "assembly"  # occupies the barista only


@dataclass(frozen=True, slots=True)
class Line:
    """One thing a customer asks for, before it becomes work.

    `variant` is the board's "Hot or Iced". It is not a garnish: the iced build
    skips the steam wand, so it changes what the drink costs at the bottleneck.
    """

    drink: str
    milk_type: str | None = None
    variant: str | None = None

    @classmethod
    def of(cls, value: "Line | tuple") -> "Line":
        return value if isinstance(value, cls) else cls(*value)


@dataclass(frozen=True, slots=True)
class Task:
    """One unit of work at one station, with its resolved service time.

    `duration_s` is filled in by `core.menu.resolve_tasks` from the station's
    cost terms; nothing downstream recomputes it.
    """

    item_id: str
    order_id: str
    station: str | None            # None for assembly: barista-only work
    kind: TaskKind
    duration_s: float
    oz: float | None = None
    shots: int | None = None
    batch_value: str | None = None  # e.g. "oat" when the station batches on milk_type

    @property
    def batchable(self) -> bool:
        return self.kind is TaskKind.PREP and self.batch_value is not None


@dataclass(slots=True)
class Item:
    """One drink or food item on an order."""

    item_id: str
    order_id: str
    drink: str
    price_cents: int
    cogs_cents: int
    requires_milk: bool
    milk_type: str | None = None
    variant: str | None = None
    tasks: list[Task] = field(default_factory=list)

    @property
    def margin_cents(self) -> int:
        return self.price_cents - self.cogs_cents

    def tasks_at(self, station: str) -> list[Task]:
        return [task for task in self.tasks if task.station == station]


@dataclass(slots=True)
class Order:
    """A basket moving through the state machine.

    `history` accumulates the events produced by `core.states.transition`. It is
    the same append-only record the log holds, never a parallel mutable summary
    (ground rule 4).
    """

    order_id: str
    channel: Channel
    placed_at_s: float
    items: list[Item] = field(default_factory=list)
    state: State = State.PLACED
    customer_id: str | None = None
    slot_id: str | None = None
    promised_at_s: float | None = None
    is_simulated: bool = False
    history: list[Event] = field(default_factory=list)

    @property
    def price_cents(self) -> int:
        return sum(item.price_cents for item in self.items)

    @property
    def margin_cents(self) -> int:
        return sum(item.margin_cents for item in self.items)

    @property
    def milk_items(self) -> list[Item]:
        return [item for item in self.items if item.requires_milk]

    @property
    def tasks(self) -> list[Task]:
        return [task for item in self.items for task in item.tasks]

    @property
    def is_terminal(self) -> bool:
        return State.is_terminal(self.state)

    def entered_at(self, state: State) -> float | None:
        """First time this order reached `state`, read from its own events."""
        for event in self.history:
            if event.to_state == state:
                return event.t_s
        return None


@dataclass(slots=True)
class Batch:
    """Items a station runs together, with the cost of running them together.

    Formed by `sim.policies`; priced by `core.capacity`. A batch of one is a
    normal task and costs exactly what the task costs.
    """

    batch_id: str
    station: str
    key: str | None
    items: list[Item] = field(default_factory=list)
    cost_s: float = 0.0

    def __len__(self) -> int:
        return len(self.items)

    @property
    def item_ids(self) -> list[str]:
        return [item.item_id for item in self.items]

    @property
    def order_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for item in self.items:
            seen.setdefault(item.order_id, None)
        return list(seen)


@dataclass(slots=True)
class Customer:
    """A person, with the two tolerances that decide whether they ever order.

    Both are drawn from `params.customers` by the simulator; the app never
    creates these.
    """

    customer_id: str
    arrived_at_s: float
    channel: Channel
    time_budget_s: float
    balk_tolerance_s: float
    order_id: str | None = None
    balked: bool = False

    def would_balk(self, estimated_wait_s: float) -> bool:
        return estimated_wait_s > self.balk_tolerance_s
