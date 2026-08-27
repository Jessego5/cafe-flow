"""Who walks in, when, and what they ask for.

Demand is class-driven: a block of sections lets out, some fraction of those
students come here, and they arrive a few minutes later spread around a mean.
Everything below is drawn from one seeded generator in a fixed order, so the
same seed reproduces the same day exactly (ground rule 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.params import SECONDS_PER_MINUTE, Params
from core.types import Channel, Line

__all__ = ["Arrival", "generate_arrivals", "class_block_size"]

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class Arrival:
    """One customer showing up, with the order they intend to place."""

    customer_id: str
    order_id: str
    at_s: float
    channel: Channel
    lines: tuple[Line, ...]
    source: str  # which class block, or "background"

    @property
    def size(self) -> int:
        return len(self.lines)


def class_block_size(block, capture_rate: float) -> int:
    """How many of a block's students turn into customers."""
    return int(round(block.sections * block.avg_enrollment * capture_rate))


def _pick(names: list[str], shares: dict[str, float], rng: np.random.Generator) -> str:
    weights = np.array([shares[name] for name in names], dtype=float)
    return names[int(rng.choice(len(names), p=weights / weights.sum()))]


def _draw_lines(params: Params, rng: np.random.Generator) -> tuple[Line, ...]:
    """One basket: an item from the mix, its milk, how it is served, and maybe
    something to eat.

    Four draws, always in this order and always all four, even when the answer
    is discarded — a conditional draw would make the seed mean different things
    for different baskets.
    """
    drink = _pick(list(params.mix.drink), params.mix.drink, rng)
    milk = _pick(list(params.mix.milk), params.mix.milk, rng)
    serve = _pick(list(params.mix.serve), params.mix.serve, rng)
    attaches = rng.random() < params.mix.attach.rate

    spec = params.menu_item(drink)
    lines = [
        Line(
            drink=drink,
            milk_type=milk if spec.requires_milk else None,
            variant=serve if spec.variants else None,
        )
    ]

    food = params.mix.attach.item
    if attaches and food != drink:
        food_spec = params.menu_item(food)
        lines.append(
            Line(
                drink=food,
                milk_type=milk if food_spec.requires_milk else None,
                variant=serve if food_spec.variants else None,
            )
        )

    return tuple(lines)


def generate_arrivals(params: Params, rng: np.random.Generator) -> list[Arrival]:
    """The whole day's demand, sorted by arrival time.

    Class blocks give the bursts; a Poisson background gives the trough. Anyone
    who would arrive outside opening hours is dropped rather than clipped to the
    edge, which would put a false spike on the boundary.
    """
    opens, closes = float(params.meta.start_s), float(params.meta.end_s)
    times: list[tuple[float, str]] = []

    for index, block in enumerate(params.arrivals.class_blocks):
        count = class_block_size(block, params.arrivals.capture_rate)
        if count <= 0:
            continue
        centre = block.ends_at_s + params.arrivals.offset_min * SECONDS_PER_MINUTE
        spread = params.arrivals.sigma_min * SECONDS_PER_MINUTE
        label = f"block{index}@{block.ends_at}"
        times.extend((float(t), label) for t in rng.normal(centre, spread, count))

    hours = (closes - opens) / SECONDS_PER_HOUR
    background = int(rng.poisson(params.arrivals.background_per_hour * hours))
    if background:
        times.extend(
            (float(t), "background") for t in rng.uniform(opens, closes, background)
        )

    times.sort(key=lambda pair: pair[0])

    arrivals: list[Arrival] = []
    for at_s, source in times:
        if not opens <= at_s < closes:
            continue
        index = len(arrivals)
        arrivals.append(
            Arrival(
                customer_id=f"c{index:05d}",
                order_id=f"o{index:05d}",
                at_s=at_s,
                channel=Channel.WALKUP,
                lines=_draw_lines(params, rng),
                source=source,
            )
        )
    return arrivals
