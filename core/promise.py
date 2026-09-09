"""
This answers when an order will be ready, and when to place one for a time you
already have in mind, which are two directions of the same question the way a
maps app answers both "leave now" and "arrive by". Forward is a lookup.
Backward is a fixed point, because the right moment to order depends on the
queue at that moment, which depends on when you order, so it is solved by
walking back from the time wanted and taking the latest start that still lands
in time. The forecast itself is not computed here but comes from the simulator
as a file, and the app reads it the way it reads params, which means a promise
made at the counter is a prediction the model actually made and promise_error
can be run over the app's own log afterwards to see whether it held. Imported
by app/ and by sim/forecast.py, which writes the file this reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

from core.menu import Line, make_order, service_seconds
from core.params import ConfigError, Params

__all__ = ["Forecast", "Promise", "load_forecast", "ready_if_ordered_now", "plan_for"]

SECONDS_PER_MINUTE = 60.0

# How finely to search for a start time is not a choice: it is the forecast's
# own resolution. Stepping finer than the bins would invent precision the
# forecast does not have, and there is nowhere earlier to look than opening.


@dataclass(frozen=True, slots=True)
class Forecast:
    """Wait by time of day, as the simulator predicted it."""

    bin_minutes: float
    opens_at_s: float
    mean_wait_s: tuple[float, ...]
    safe_wait_s: tuple[float, ...]
    # No default: a forecast that does not say which quantile it was quoted at
    # cannot be reasoned about, and a promise built on an unknown one is a
    # guess wearing a uniform.
    quantile: int
    seeds: int = 0
    scenario: str = ""

    def bin_for(self, at_s: float) -> int:
        width = self.bin_minutes * SECONDS_PER_MINUTE
        index = int((at_s - self.opens_at_s) // width)
        return max(0, min(len(self.mean_wait_s) - 1, index))

    def wait_at(self, at_s: float, *, safe: bool = True) -> float:
        series = self.safe_wait_s if safe else self.mean_wait_s
        return series[self.bin_for(at_s)]

    @property
    def closes_at_s(self) -> float:
        return self.opens_at_s + len(self.mean_wait_s) * self.bin_minutes * SECONDS_PER_MINUTE

    @classmethod
    def from_dict(cls, data: dict) -> "Forecast":
        try:
            body = data["forecast"]
            return cls(
                bin_minutes=float(body["bin_minutes"]),
                opens_at_s=float(body["opens_at_s"]),
                mean_wait_s=tuple(float(v) for v in body["mean_wait_s"]),
                safe_wait_s=tuple(float(v) for v in body["safe_wait_s"]),
                quantile=int(body["quantile"]),
                seeds=int(body.get("seeds", 0)),
                scenario=str(body.get("scenario", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"not a forecast: {exc}") from None


def load_forecast(path: str | Path) -> Forecast:
    target = Path(path)
    if not target.exists():
        raise ConfigError(f"no forecast at {target}: run `python -m sim.forecast`")
    return Forecast.from_dict(yaml.safe_load(target.read_text()))


@dataclass(frozen=True, slots=True)
class Promise:
    """A quoted time, and enough of its reasoning to argue with."""

    order_at_s: float
    ready_at_s: float
    wait_s: float
    basket_s: float
    typical_basket_s: float
    wanted_at_s: float | None = None
    achievable: bool = True

    @property
    def slack_s(self) -> float:
        """How much earlier than asked for. Negative means it cannot be done."""
        return 0.0 if self.wanted_at_s is None else self.wanted_at_s - self.ready_at_s


def typical_basket_seconds(params: Params) -> float:
    """
    What an average order takes, mix-weighted. The forecast is quoted for
    one of these, so a heavier basket is charged the difference."""
    from core.menu import make_item

    total = 0.0
    for name, share in params.mix.drink.items():
        spec = params.menu_item(name)
        milk = next(iter(params.mix.milk)) if spec.requires_milk else None
        item = make_item(name, params, order_id="typical", item_id=f"typical-{name}",
                         milk_type=milk)
        total += share * service_seconds(item)
    return total


def basket_seconds(params: Params, lines: Sequence[Line | tuple]) -> float:
    order = make_order("quote", params, lines=list(lines))
    return sum(service_seconds(item) for item in order.items)


def _wait_for(
    forecast: Forecast, params: Params, at_s: float, basket_s: float, typical_s: float
) -> float:
    """
    The forecast wait, adjusted for a basket heavier or lighter than average.

    The forecast is an average over the mix, so an order of three paninis is not
    the order it was quoted for.
    """
    return max(0.0, forecast.wait_at(at_s) + (basket_s - typical_s))


def ready_if_ordered_now(
    forecast: Forecast, params: Params, lines: Sequence[Line | tuple], now_s: float
) -> Promise:
    """Order now, collect when."""
    basket = basket_seconds(params, lines)
    typical = typical_basket_seconds(params)
    wait = _wait_for(forecast, params, now_s, basket, typical)
    return Promise(
        order_at_s=now_s,
        ready_at_s=now_s + wait,
        wait_s=wait,
        basket_s=basket,
        typical_basket_s=typical,
    )


def plan_for(
    forecast: Forecast,
    params: Params,
    lines: Sequence[Line | tuple],
    wanted_at_s: float,
    now_s: float,
) -> Promise:
    """
    Want it at a particular time: when to order.

    Walks back from the time wanted and takes the latest start that still lands
    in time. Latest rather than earliest, because ordering sooner than necessary
    only means the drink sits on the shelf getting cold, and because the whole
    point is to move the order into the trough, not out of the day.

    The walk always ends on `earliest` rather than stepping past it. Stepping in
    the forecast's own bins from the time wanted means the grid rarely lands on
    now, so a request less than one bin ahead used to be refused with a quote
    that was in time: "I want it in five minutes" got the earliest-we-can-do
    answer while the drink would have been ready in three. Ordering this instant
    is always the earliest a start can be, so it is always the last candidate.
    """
    basket = basket_seconds(params, lines)
    typical = typical_basket_seconds(params)

    step_s = forecast.bin_minutes * SECONDS_PER_MINUTE
    earliest = max(now_s, forecast.opens_at_s)

    best: Promise | None = None
    at = max(wanted_at_s, earliest)
    while True:
        wait = _wait_for(forecast, params, at, basket, typical)
        if at + wait <= wanted_at_s:
            best = Promise(
                order_at_s=at,
                ready_at_s=at + wait,
                wait_s=wait,
                basket_s=basket,
                typical_basket_s=typical,
                wanted_at_s=wanted_at_s,
            )
            break
        if at <= earliest:
            break
        at = max(earliest, at - step_s)

    if best is not None:
        return best

    # Even ordering this instant does not make it. Say so rather than quoting a
    # time that was never available.
    now = ready_if_ordered_now(forecast, params, lines, now_s)
    return Promise(
        order_at_s=now.order_at_s,
        ready_at_s=now.ready_at_s,
        wait_s=now.wait_s,
        basket_s=basket,
        typical_basket_s=typical,
        wanted_at_s=wanted_at_s,
        achievable=False,
    )
