"""Who walks in, when, and what they ask for.

Demand is class-driven: a block of sections lets out, some fraction of those
students come here, and they arrive a few minutes later spread around a mean.
Everything below is drawn from one seeded generator in a fixed order, so the
same seed reproduces the same day exactly (ground rule 5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.params import SECONDS_PER_MINUTE, Params, format_hhmm
from core.types import Channel, Line
from sim.balking import draw_channel, preorder_lead_s, sample_minutes

__all__ = ["Arrival", "generate_arrivals", "class_block_size"]

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class Arrival:
    """One customer, with the order they intend to place and what they will
    put up with to get it.

    `at_s` is when the order is *placed*. For someone ordering ahead that is a
    class block earlier than `wanted_at_s`, which is when they turn up for it.
    """

    customer_id: str
    order_id: str
    at_s: float
    channel: Channel
    lines: tuple[Line, ...]
    source: str  # which class block, or "background"
    balk_tolerance_s: float = float("inf")
    time_budget_s: float = float("inf")
    wanted_at_s: float | None = None
    no_show: bool = False
    defer_roll: float = 1.0

    @property
    def size(self) -> int:
        return len(self.lines)

    @property
    def preordered(self) -> bool:
        return self.channel is Channel.PREORDER


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


def _from_class_blocks(params: Params, rng: np.random.Generator) -> list[tuple[float, str]]:
    """Demand as a timetable: a block lets out, some fraction come here."""
    times: list[tuple[float, str]] = []
    for index, block in enumerate(params.arrivals.class_blocks):
        count = class_block_size(block, params.arrivals.capture_rate)
        if count <= 0:
            continue
        centre = block.ends_at_s + params.arrivals.offset_min * SECONDS_PER_MINUTE
        spread = params.arrivals.sigma_min * SECONDS_PER_MINUTE
        label = f"block{index}@{block.ends_at}"
        times.extend((float(t), label) for t in rng.normal(centre, spread, count))
    return times


def _from_profile(params: Params, rng: np.random.Generator) -> list[tuple[float, str]]:
    """Demand as a measured curve.

    A non-homogeneous Poisson process: each bin's count is drawn from its own
    rate and placed uniformly inside it. `capture_rate` still scales the whole
    curve, so the same knob calibrates either model — which is what lets a
    measured shape and a fitted volume live together.
    """
    profile = params.arrivals.profile
    opens, closes = float(params.meta.start_s), float(params.meta.end_s)
    width_s = profile.bin_minutes * SECONDS_PER_MINUTE
    scale = params.arrivals.capture_rate

    times: list[tuple[float, str]] = []
    for index, rate in enumerate(profile.rate_per_hour):
        start = opens + index * width_s
        end = min(start + width_s, closes)
        if start >= closes or end <= start or rate <= 0:
            continue
        expected = rate * scale * (end - start) / SECONDS_PER_HOUR
        count = int(rng.poisson(expected))
        if count:
            label = f"profile@{format_hhmm(start)}"
            times.extend((float(t), label) for t in rng.uniform(start, end, count))
    return times


def generate_arrivals(params: Params, rng: np.random.Generator) -> list[Arrival]:
    """The whole day's demand, sorted by arrival time.

    Two ways of getting there, chosen by `arrivals.model`: a timetable of class
    blocks, or a curve measured from a transaction log. Either way a Poisson
    background fills the trough, and anyone who would arrive outside opening
    hours is dropped rather than clipped to the edge, which would put a false
    spike on the boundary.
    """
    opens, closes = float(params.meta.start_s), float(params.meta.end_s)

    if params.arrivals.model == "profile":
        times = _from_profile(params, rng)
    else:
        times = _from_class_blocks(params, rng)

    hours = (closes - opens) / SECONDS_PER_HOUR
    background = int(rng.poisson(params.arrivals.background_per_hour * hours))
    if background:
        times.extend(
            (float(t), "background") for t in rng.uniform(opens, closes, background)
        )

    times.sort(key=lambda pair: pair[0])
    lead_s = preorder_lead_s(params)

    drawn: list[Arrival] = []
    for wanted_at_s, source in times:
        if not opens <= wanted_at_s < closes:
            continue

        # Fixed draw order, and every draw made for every customer even when
        # the answer is discarded: a conditional draw would make the seed mean
        # different things for different people.
        lines = _draw_lines(params, rng)
        channel = draw_channel(params, rng)
        tolerance_s = sample_minutes(params.customers.balk_tolerance_min, rng) * SECONDS_PER_MINUTE
        budget_s = sample_minutes(params.customers.time_budget_min, rng) * SECONDS_PER_MINUTE
        no_show = rng.random() < params.customers.no_show_rate
        defer_roll = float(rng.random())

        placed_at_s = (
            max(opens, wanted_at_s - lead_s)
            if channel is Channel.PREORDER
            else wanted_at_s
        )

        drawn.append(
            Arrival(
                customer_id="",
                order_id="",
                at_s=placed_at_s,
                channel=channel,
                lines=lines,
                source=source,
                balk_tolerance_s=tolerance_s,
                time_budget_s=budget_s,
                wanted_at_s=wanted_at_s,
                no_show=no_show and channel is Channel.PREORDER,
                defer_roll=defer_roll,
            )
        )

    # Ordering ahead moves someone earlier in the day, so identities are only
    # settled once the whole day is in placement order.
    drawn.sort(key=lambda arrival: (arrival.at_s, arrival.wanted_at_s))
    return [
        Arrival(
            customer_id=f"c{index:05d}",
            order_id=f"o{index:05d}",
            at_s=arrival.at_s,
            channel=arrival.channel,
            lines=arrival.lines,
            source=arrival.source,
            balk_tolerance_s=arrival.balk_tolerance_s,
            time_budget_s=arrival.time_budget_s,
            wanted_at_s=arrival.wanted_at_s,
            no_show=arrival.no_show,
            defer_roll=arrival.defer_roll,
        )
        for index, arrival in enumerate(drawn)
    ]
