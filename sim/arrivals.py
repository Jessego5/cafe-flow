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
from core.types import Channel

__all__ = ["Arrival", "generate_arrivals", "class_block_size"]

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class Arrival:
    """One customer showing up, with the order they intend to place."""

    customer_id: str
    order_id: str
    at_s: float
    channel: Channel
    lines: tuple[tuple[str, str | None], ...]
    source: str  # which class block, or "background"

    @property
    def size(self) -> int:
        return len(self.lines)


def class_block_size(block, capture_rate: float) -> int:
    """How many of a block's students turn into customers."""
    return int(round(block.sections * block.avg_enrollment * capture_rate))


def _draw_lines(
    params: Params, rng: np.random.Generator
) -> tuple[tuple[str, str | None], ...]:
    """One basket: a drink from the mix, its milk, and maybe something to eat.

    The draw order is fixed because changing it would change every subsequent
    number for the same seed.
    """
    drinks = list(params.mix.drink)
    weights = np.array([params.mix.drink[name] for name in drinks], dtype=float)
    drink = drinks[int(rng.choice(len(drinks), p=weights / weights.sum()))]

    milk = None
    if params.menu_item(drink).requires_milk:
        milks = list(params.mix.milk)
        milk_weights = np.array([params.mix.milk[name] for name in milks], dtype=float)
        milk = milks[int(rng.choice(len(milks), p=milk_weights / milk_weights.sum()))]

    lines: list[tuple[str, str | None]] = [(drink, milk)]

    attaches = rng.random() < params.mix.attach_rate
    if attaches:
        food = next(
            (name for name, spec in params.menu.items() if not spec.requires_milk
             and any(task.station == "oven" for task in spec.tasks)),
            None,
        )
        if food is not None and food != drink:
            lines.append((food, None))

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
