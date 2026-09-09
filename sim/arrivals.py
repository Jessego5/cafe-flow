"""
This decides who walks in, when, and what they ask for. Demand is class-driven
rather than a flat rate: a block of sections lets out, some fraction of those
students come here, and they arrive a few minutes later spread around a mean,
which is what gives the day the shape the registrar's room schedule implies.
Everything is drawn from one seeded generator in a fixed order, so the same
seed reproduces the same day exactly and a diff between two runs means a real
change rather than a different roll. Imported by sim/engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.params import ConfigError, SECONDS_PER_MINUTE, Params, format_hhmm
from core.promise import Forecast, load_forecast
from core.types import Channel, Line
from sim.balking import draw_channel, preorder_lead_s, sample_minutes

__all__ = ["Arrival", "generate_arrivals", "class_block_size"]

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class Arrival:
    """
    One customer, with the order they intend to place and what they will
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


def line_of(params: Params, name: str, milk: str, serve: str) -> Line:
    """One menu item as a line, taking milk and serve only where they apply."""
    spec = params.menu_item(name)
    return Line(
        drink=name,
        milk_type=milk if spec.requires_milk else None,
        variant=serve if spec.variants else None,
    )


def _draw_lines(params: Params, rng: np.random.Generator) -> tuple[Line, ...]:
    """
    One basket: how many things, whether one of them is food, and what.

    Food is an attachment rather than a first choice: drawing every basket's
    first item from the whole menu made a quarter of all orders a sandwich with
    nobody buying a coffee. But it is an attachment that makes the basket
    bigger: of the orders counted at the till, 11% of single items were food
    against 88% of pairs. So the size comes first and the food follows from it.

    `mix.basket` says both. Without it the older `mix.attach` still works, which
    is what the example configurations use; that form can only ever add food to
    one drink, so it cannot produce two drinks or three items.

    A fixed number of draws, always in the same order and always all of them,
    even where the answer is discarded, because a conditional draw would make the seed
    mean different things for different baskets.
    """
    drinks, foods = params.split_mix()
    basket = params.mix.basket
    most = basket.largest if basket is not None else 2

    milk = _pick(list(params.mix.milk), params.mix.milk, rng)
    serve = _pick(list(params.mix.serve), params.mix.serve, rng)
    picked_drinks = [_pick(list(drinks), drinks, rng) for _ in range(most)]
    picked_food = _pick(list(foods), foods, rng) if foods else None
    size_roll = float(rng.random())
    food_roll = float(rng.random())
    alone_roll = float(rng.random())

    food = params.mix.attach.item or picked_food

    if basket is None:
        # The older form: one drink, and maybe food beside it.
        if food is None:
            return (line_of(params, picked_drinks[0], milk, serve),)
        if alone_roll < params.mix.attach.alone:
            return (line_of(params, food, milk, serve),)
        lines = [line_of(params, picked_drinks[0], milk, serve)]
        if food_roll < params.mix.attach.rate and food != picked_drinks[0]:
            lines.append(line_of(params, food, milk, serve))
        return tuple(lines)

    size = 1
    running = 0.0
    for index, share in enumerate(basket.size):
        running += share
        if size_roll < running:
            size = index + 1
            break
    else:
        size = basket.largest

    has_food = food is not None and food_roll < basket.food[size - 1]
    names = picked_drinks[: size - 1] + [food] if has_food else picked_drinks[:size]
    return tuple(line_of(params, name, milk, serve) for name in names)


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
    """
    Demand as a measured curve.

    A non-homogeneous Poisson process: each bin's count is drawn from its own
    rate and placed uniformly inside it. `capture_rate` still scales the whole
    curve, so the same knob calibrates either model, which is what lets a
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


def _forecast_for(params: Params) -> "Forecast | None":
    """
    The wait-by-time-of-day the app would be showing, or None.

    Only loaded when somebody is going to act on it. A cafe not offering
    arrive-by has no forecast in front of anyone, and a missing file is then not
    an error: it is a cafe that has not run `python -m sim.forecast`.
    """
    if params.customers.retime_fraction <= 0:
        return None
    try:
        return load_forecast("params/forecast.yaml")
    except ConfigError:
        return None


def _retimed(
    wanted_at_s: float, forecast: "Forecast", params: Params
) -> float:
    """
    The slot this customer picks once they can see what each one costs.

    The planner shows the wait at every time of day. Somebody who wanted 12:15,
    sees eleven minutes there and three at 12:45, and is not in a hurry, takes
    12:45, which is the whole economic argument for arrive-by, and the only
    lever in this project that moves demand rather than rearranging it.

    Scans forward in the forecast's own bins and takes the first under the
    threshold. Returns the original time when nothing inside the window is
    better, because somebody who cannot find a quieter slot does not invent one.
    """
    threshold_s = params.customers.retime_threshold_min * SECONDS_PER_MINUTE
    window_s = params.customers.retime_window_min * SECONDS_PER_MINUTE
    step_s = forecast.bin_minutes * SECONDS_PER_MINUTE

    if forecast.wait_at(wanted_at_s) <= threshold_s:
        return wanted_at_s
    at = wanted_at_s + step_s
    while at <= min(wanted_at_s + window_s, params.meta.end_s):
        if forecast.wait_at(at) <= threshold_s:
            return at
        at += step_s
    return wanted_at_s


def generate_arrivals(params: Params, rng: np.random.Generator) -> list[Arrival]:
    """
    The whole day's demand, sorted by arrival time.

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
    forecast = _forecast_for(params)

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

        # Somebody ordering ahead is choosing a time, not accepting one. Where
        # the app shows them what each costs, a share of them move off the peak,
        # deferred demand rather than lost demand, and the only thing here
        # that changes *when* people come rather than how they are served.
        if (
            forecast is not None
            and channel is Channel.PREORDER
            and defer_roll < params.customers.retime_fraction
        ):
            wanted_at_s = _retimed(wanted_at_s, forecast, params)

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
