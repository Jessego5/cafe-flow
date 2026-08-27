"""Menu -> station plan resolution.

The only place a drink name becomes work. `app/` shows prices from here and
`sim/` times drinks from here, so the two cannot disagree about what a latte is
(ground rule 2).
"""

from __future__ import annotations

from typing import Iterable, Sequence

from core.capacity import task_seconds
from core.params import ConfigError, Params
from core.types import Channel, Item, Order, Task, TaskKind

__all__ = [
    "resolve_tasks",
    "make_item",
    "make_order",
    "service_seconds",
    "price_cents",
]


def resolve_tasks(item: Item, params: Params) -> list[Task]:
    """The station plan for one item, with service times already resolved.

    Prep tasks in menu order, then the barista-only assembly task. Durations
    include per-batch setup, because an item made on its own pays setup; batching
    discounts are applied later by `core.capacity`.
    """
    spec = params.menu_item(item.drink)
    tasks: list[Task] = []

    for task_spec in spec.tasks:
        station = params.station(task_spec.station)
        batch_value = None
        if station.batch_key is not None:
            batch_value = getattr(item, station.batch_key, None)
            batch_value = None if batch_value is None else str(batch_value)
        tasks.append(
            Task(
                item_id=item.item_id,
                order_id=item.order_id,
                station=task_spec.station,
                kind=TaskKind.PREP,
                duration_s=task_seconds(station, oz=task_spec.oz, shots=task_spec.shots),
                oz=task_spec.oz,
                shots=task_spec.shots,
                batch_value=batch_value,
            )
        )

    if spec.assembly_s > 0:
        tasks.append(
            Task(
                item_id=item.item_id,
                order_id=item.order_id,
                station=None,
                kind=TaskKind.ASSEMBLY,
                duration_s=spec.assembly_s,
            )
        )
    return tasks


def make_item(
    drink: str,
    params: Params,
    *,
    order_id: str,
    item_id: str,
    milk_type: str | None = None,
) -> Item:
    """Build an item with its price, margin and station plan attached."""
    spec = params.menu_item(drink)

    if spec.requires_milk and milk_type is None:
        raise ConfigError(f"{drink} requires a milk type")
    if not spec.requires_milk and milk_type is not None:
        raise ConfigError(f"{drink} takes no milk, got {milk_type!r}")
    if milk_type is not None and milk_type not in params.mix.milk:
        raise ConfigError(
            f"unknown milk {milk_type!r} (have {sorted(params.mix.milk)})"
        )

    item = Item(
        item_id=item_id,
        order_id=order_id,
        drink=drink,
        price_cents=spec.price_cents,
        cogs_cents=spec.cogs_cents,
        requires_milk=spec.requires_milk,
        milk_type=milk_type,
    )
    item.tasks = resolve_tasks(item, params)
    return item


def make_order(
    order_id: str,
    params: Params,
    *,
    lines: Sequence[tuple[str, str | None]],
    channel: Channel = Channel.WALKUP,
    placed_at_s: float = 0.0,
    customer_id: str | None = None,
    is_simulated: bool = False,
) -> Order:
    """Build an order from `(drink, milk_type)` lines.

    Item ids are derived from the order id so a replayed scenario names its
    items identically every run (ground rule 5).
    """
    if not lines:
        raise ConfigError(f"order {order_id} has no items")

    order = Order(
        order_id=order_id,
        channel=Channel(channel),
        placed_at_s=placed_at_s,
        customer_id=customer_id,
        is_simulated=is_simulated,
    )
    order.items = [
        make_item(
            drink,
            params,
            order_id=order_id,
            item_id=f"{order_id}-{index}",
            milk_type=milk,
        )
        for index, (drink, milk) in enumerate(lines)
    ]
    return order


def service_seconds(item: Item) -> float:
    """Hands-on seconds for one item made alone, across every station."""
    return sum(task.duration_s for task in item.tasks)


def order_service_seconds(order: Order) -> float:
    return sum(service_seconds(item) for item in order.items)


def price_cents(lines: Iterable[tuple[str, str | None]], params: Params) -> int:
    return sum(params.menu_item(drink).price_cents for drink, _ in lines)
