"""The bottleneck cost function.

Everything that meters capacity — slot sizing in `app/`, batching gains in
`sim/policies.py`, utilisation in `analysis/` — asks this module how many
bottleneck-seconds a thing costs. Which station that is comes from
`params.bottleneck_station`, so M8 can move it (to `register`, say) by editing
YAML. When the bottleneck is the register the cost is flat per order and slots
degenerate to order counts, with no code change.
"""

from __future__ import annotations

from typing import Iterable, Protocol, Sequence, runtime_checkable

from core.params import (
    OUNCES_PER_STEAM_UNIT,
    PER_BATCH,
    PER_ITEM,
    PER_ORDER,
    ConfigError,
    Params,
    StationParams,
)
from core.types import Batch, Item, Order

__all__ = ["CapacityModel", "StationCapacityModel", "task_seconds"]


def _scale(attr: str | None, oz: float | None, shots: int | None) -> float:
    """How many times a per-item term is paid for one task."""
    if attr is None:
        return 1.0
    if attr == "oz":
        if oz is None:
            raise ConfigError("station charges per ounce but the task carries no `oz`")
        return oz / OUNCES_PER_STEAM_UNIT
    if attr == "shots":
        if shots is None:
            raise ConfigError("station charges per shot but the task carries no `shots`")
        return float(shots)
    raise ConfigError(f"unknown cost scale {attr!r}")


def task_seconds(
    station: StationParams,
    *,
    oz: float | None = None,
    shots: int | None = None,
    include_batch_terms: bool = True,
    include_order_terms: bool = False,
) -> float:
    """Service time for one task run on its own.

    Per-batch terms (setup) are included by default because a task run alone
    still pays setup. Per-order terms (the register) are not part of a drink's
    make time and stay off unless asked for.
    """
    total = 0.0
    for seconds, per, attr in station.cost_terms.values():
        if per == PER_ITEM:
            total += seconds * _scale(attr, oz, shots)
        elif per == PER_BATCH and include_batch_terms:
            total += seconds
        elif per == PER_ORDER and include_order_terms:
            total += seconds
    return total


@runtime_checkable
class CapacityModel(Protocol):
    def cost(self, item: Item) -> float:
        """Bottleneck-seconds consumed by this item."""

    def batch_cost(self, items: Sequence[Item]) -> float:
        """Cost when made together. <= sum of individual costs."""


class StationCapacityModel:
    """Costs measured at one station, read from params.

    Reads `bottleneck_station` unless told otherwise, so the same class also
    answers "how much group-head time does this order need" during M8's
    occupancy analysis.
    """

    def __init__(self, params: Params, station_name: str | None = None) -> None:
        self.params = params
        self.station_name = station_name or params.bottleneck_station
        self.station = params.station(self.station_name)

    # ---- single items --------------------------------------------------

    def cost(self, item: Item) -> float:
        """Bottleneck-seconds for one item ordered on its own."""
        return self._work_seconds([item]) + self._order_seconds([item])

    def batch_cost(self, items: Sequence[Item]) -> float:
        """Bottleneck-seconds when these items are made together.

        Setup is paid once per batch instead of once per item, and per-order
        terms once per distinct order, so this can only ever be <= the sum of
        the individual costs.
        """
        if not items:
            return 0.0
        return sum(self._batch_work_seconds(batch) for batch in self.group(items)) + (
            self._order_seconds(items)
        )

    def order_cost(self, order: Order) -> float:
        return self.batch_cost(order.items)

    # ---- grouping ------------------------------------------------------

    def batch_value(self, item: Item) -> str | None:
        """The value this station batches on, e.g. the item's milk type."""
        key = self.station.batch_key
        if key is None:
            return None
        if not hasattr(item, key):
            raise ConfigError(
                f"station {self.station_name!r} batches on {key!r} "
                f"but Item has no such attribute"
            )
        value = getattr(item, key)
        return None if value is None else str(value)

    def item_oz(self, item: Item) -> float:
        return sum(task.oz or 0.0 for task in item.tasks_at(self.station_name))

    @property
    def order_scoped(self) -> bool:
        """Does this station handle whole orders rather than named tasks?

        The register does: no menu item lists it, but every order goes through
        it and every item on the order is rung up.
        """
        return self.station.base_s is not None

    def touches(self, item: Item) -> bool:
        """Does this item do any work at this station?"""
        return bool(item.tasks_at(self.station_name)) or self.order_scoped

    def group(self, items: Sequence[Item], batch_id_prefix: str = "b") -> list[Batch]:
        """Split items into the batches this station would actually run.

        Deterministic: input order is preserved and groups fill greedily, so the
        same pending queue always yields the same batches (ground rule 5).
        """
        working = [item for item in items if self.touches(item)]
        if not working:
            return []

        groups: dict[str | None, list[list[Item]]] = {}
        order: list[str | None] = []
        limit_oz = self.station.max_batch_oz
        limit_n = self.station.batch_size
        batches_at_all = limit_oz is not None or limit_n is not None or self.station.batch_key

        for item in working:
            value = self.batch_value(item)
            if value not in groups:
                groups[value] = []
                order.append(value)
            bucket = groups[value]
            if not batches_at_all or not bucket or not self._fits(bucket[-1], item):
                bucket.append([item])
            else:
                bucket[-1].append(item)

        out: list[Batch] = []
        for value in order:
            for members in groups[value]:
                out.append(
                    Batch(
                        batch_id=f"{batch_id_prefix}{len(out)}",
                        station=self.station_name,
                        key=value,
                        items=list(members),
                        cost_s=self._batch_work_seconds_items(members),
                    )
                )
        return out

    def compatible(self, current: Sequence[Item], candidate: Item) -> bool:
        """Could this item join that batch?

        The rule lives here rather than in a scheduling policy: whether two
        drinks can be steamed together is a fact about the station, not a
        choice about the order of work (ground rule 2).
        """
        if not self.touches(candidate):
            return False
        if not current:
            return True
        if not self.station.batches:
            return False
        if self.batch_value(candidate) != self.batch_value(current[0]):
            return False
        return self._fits(list(current), candidate)

    def _fits(self, current: list[Item], candidate: Item) -> bool:
        limit_n = self.station.batch_size
        if limit_n is not None and len(current) >= limit_n:
            return False
        limit_oz = self.station.max_batch_oz
        if limit_oz is not None:
            used = sum(self.item_oz(item) for item in current)
            if used + self.item_oz(candidate) > limit_oz:
                return False
        return True

    # ---- seconds -------------------------------------------------------

    def _item_seconds(self, item: Item) -> float:
        """Per-item terms, summed over this item's tasks at the station.

        An order-scoped station has no named tasks, so its flat per-item terms
        are charged once for the item itself: the register's few seconds an item
        are paid whether or not the item involves any work there.
        """
        tasks = item.tasks_at(self.station_name)
        if not tasks:
            if not self.order_scoped:
                return 0.0
            return sum(
                seconds
                for seconds, per, attr in self.station.cost_terms.values()
                if per == PER_ITEM and attr is None
            )
        return sum(
            task_seconds(
                self.station,
                oz=task.oz,
                shots=task.shots,
                include_batch_terms=False,
            )
            for task in tasks
        )

    def _batch_terms(self) -> float:
        return sum(
            seconds
            for seconds, per, _ in self.station.cost_terms.values()
            if per == PER_BATCH
        )

    def _batch_work_seconds_items(self, items: Sequence[Item]) -> float:
        if not items:
            return 0.0
        return self._batch_terms() + sum(self._item_seconds(item) for item in items)

    def _batch_work_seconds(self, batch: Batch) -> float:
        return self._batch_work_seconds_items(batch.items)

    def _work_seconds(self, items: Sequence[Item]) -> float:
        """Every item on its own: setup paid once per item."""
        return sum(
            self._batch_work_seconds_items([item]) for item in items if self.touches(item)
        )

    def _order_seconds(self, items: Iterable[Item]) -> float:
        """Per-order terms, paid once per distinct order id."""
        per_order = sum(
            seconds
            for seconds, per, _ in self.station.cost_terms.values()
            if per == PER_ORDER
        )
        if not per_order:
            return 0.0
        distinct = {item.order_id for item in items}
        return per_order * len(distinct)

    # ---- slot plumbing (M2) -------------------------------------------

    def capacity_seconds(self, window_s: float, t_s: float | None = None) -> float:
        """Bottleneck-seconds a window of wall time provides.

        Multiplies by the station's parallelism, resolving `from_staffing`
        against the staffing plan when a time is given.
        """
        return window_s * self.params.station_capacity(self.station_name, t_s)
