"""How long the queue looks, to someone standing in front of it.

In `core/` because both halves need the same answer. The simulator asks it to
decide who gives up; the app asks it to put a number on a screen. If those two
ever disagreed, the cafe would be telling customers one thing and the model
would be assuming another (ground rule 2).

It is deliberately the estimate a *person* could make: people ahead, times how
long each looks like taking. Not the best forecast available from the state of
every station. Nobody standing at a counter computes anything better, and the
number is only useful if it is the one a customer would arrive at themselves.
"""

from __future__ import annotations

from typing import Iterable

from core.menu import make_item, service_seconds
from core.params import Params
from core.states import State

__all__ = [
    "WAITING_STATES",
    "nominal_seconds_per_order",
    "observable_queue_depth",
    "estimate_wait_s",
]

#: An order is part of the line a customer sees themselves joining until it
#: reaches the shelf. What is already made is not something to wait behind.
WAITING_STATES = frozenset({State.PLACED, State.ACCEPTED, State.IN_PROGRESS})


def nominal_seconds_per_order(params: Params, baristas: int) -> float:
    """What one person in the line is worth, roughly.

    Derived rather than configured: the mix-weighted hands-on time of an order
    plus the register, divided by the people working. A first-order estimate,
    and meant to be: it stands in for what someone can infer from watching the
    counter, not for what the cafe actually achieves.
    """
    per_item: list[float] = []
    for name, share in params.mix.drink.items():
        spec = params.menu_item(name)
        milk = next(iter(params.mix.milk)) if spec.requires_milk else None
        item = make_item(
            name, params, order_id="nominal", item_id=f"nominal-{name}", milk_type=milk
        )
        per_item.append(share * service_seconds(item))

    register = params.station("register")
    ringing_up = (register.base_s or 0.0) + (register.per_item_s or 0.0)
    return (sum(per_item) + ringing_up) / max(1, baristas)


def observable_queue_depth(states: Iterable) -> int:
    """How many people are visibly still waiting."""
    return sum(1 for state in states if State(state) in WAITING_STATES)


def estimate_wait_s(depth: int, seconds_per_order: float) -> float:
    """People ahead times how long each looks like taking."""
    return max(0, depth) * seconds_per_order
