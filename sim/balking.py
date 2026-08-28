"""Whether a customer joins the queue at all, and by which channel.

Until now the model assumed infinite patience: nobody ever left, so a long
queue cost nothing and throughput was a ceiling nobody would reach in practice.
This is where a wait acquires a consequence.

A walk-up arrives, looks at the line, estimates what it will cost them, and
leaves if that exceeds what they will put up with. Someone who ordered ahead has
already committed and never balks — which is the whole of the pre-order case,
and why the balk count is the revenue argument.
"""

from __future__ import annotations

from statistics import mean

import numpy as np

from core.params import SECONDS_PER_MINUTE, ConfigError, Params
from core.types import Channel

__all__ = [
    "sample_minutes",
    "nominal_seconds_per_order",
    "estimate_wait_s",
    "observable_queue_depth",
]


def sample_minutes(dist, rng: np.random.Generator) -> float:
    """Draw from one of the distributions the config allows, in minutes."""
    kind = dist.dist
    if kind == "lognormal":
        return float(rng.lognormal(mean=np.log(dist.median), sigma=dist.sigma))
    if kind == "normal":
        return float(rng.normal(dist.mean, dist.sigma))
    if kind == "constant":
        return float(dist.value)
    raise ConfigError(f"cannot draw from distribution {kind!r}")


def nominal_seconds_per_order(params: Params, baristas: int) -> float:
    """How long one person in the line is worth, roughly.

    Derived rather than configured: the mix-weighted hands-on time of an order,
    divided by the number of people working. It is a first-order estimate and
    is meant to be — it stands in for what a customer can infer from watching
    the counter, not for what the cafe actually achieves.
    """
    from core.menu import make_item, service_seconds

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


def estimate_wait_s(depth: int, seconds_per_order: float) -> float:
    """What the customer thinks the queue will cost them.

    People ahead times how long each looks like taking. Nobody standing at a
    counter computes anything better than this.
    """
    return max(0, depth) * seconds_per_order


def observable_queue_depth(states) -> int:
    """How many people are visibly still waiting for their order.

    Anything already on the handoff shelf is not part of the line a customer
    sees themselves joining.
    """
    from core.states import State

    waiting = {State.PLACED, State.ACCEPTED, State.IN_PROGRESS}
    return sum(1 for state in states if State(state) in waiting)


def draw_channel(params: Params, rng: np.random.Generator) -> Channel:
    """Walk up, or order ahead. Adoption is a config fraction today and an
    observable at the pickup shelf once someone counts."""
    return (
        Channel.PREORDER
        if rng.random() < params.customers.preorder_adoption
        else Channel.WALKUP
    )


def preorder_lead_s(params: Params) -> float:
    """How far ahead someone orders: one class block.

    Taken from the gap between the blocks themselves, so a timetable with
    90-minute periods moves this without touching any code.
    """
    ends = sorted(block.ends_at_s for block in params.arrivals.class_blocks)
    gaps = [later - earlier for earlier, later in zip(ends, ends[1:])]
    if not gaps:
        # no timetable to take it from, so an hour: long enough to be ordering
        # ahead rather than queueing, short enough that the drink is still worth
        # collecting
        return 60.0 * SECONDS_PER_MINUTE
    return float(mean(gaps))
