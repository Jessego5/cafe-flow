"""
This computes what the wait will be by time of day, because the app has to
answer two questions a customer actually asks, if I order now when do I get it,
and if I want it at quarter past when should I order, and both need a wait it
can look up rather than one it can only measure after the fact. The forecast
comes from the simulator, many days averaged and binned by time of day, and is
written out as a file the app reads; the app never imports the simulator but
reads a forecast the way it reads params and the policy selection, which is
what makes a promise at the counter a prediction the model actually made and
lets promise_error be run over the app's own log afterwards to see whether it
held. Each bin carries two numbers, the mean wait and a high quantile, and the
second is what an honest promise is built on, because quoting the mean means
missing half of them. Run it with python -m sim.forecast, which writes
params/forecast.yaml.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from statistics import mean

import numpy as np
import yaml

from core.params import Params, load_params
from core.promise import Forecast
from core.states import State

__all__ = ["Forecast", "forecast", "to_dict", "SAFE_QUANTILE"]

SECONDS_PER_MINUTE = 60.0

# Promises are quoted off this rather than the mean. A promise kept on average
# is missed half the time, and a promise missed half the time is not one anyone
# relies on twice.
SAFE_QUANTILE = 80


def to_dict(result: Forecast) -> dict:
    """The file the app reads. `core.promise.Forecast` reads it back."""
    return {
        "forecast": {
            "scenario": result.scenario,
            "seeds": result.seeds,
            "bin_minutes": result.bin_minutes,
            "opens_at_s": result.opens_at_s,
            "quantile": SAFE_QUANTILE,
            "mean_wait_s": [round(v, 1) for v in result.mean_wait_s],
            "safe_wait_s": [round(v, 1) for v in result.safe_wait_s],
        }
    }


def forecast(
    params: Params, runner, *, seeds: list[int] | None = None, bin_minutes: float = 15.0
) -> Forecast:
    """
    Wait by time of day, from many simulated days.

    Binned by when the order was *placed*, because that is the moment a customer
    is deciding, and it is the only time they can act on.
    """
    seeds = seeds or list(range(24))
    width = bin_minutes * SECONDS_PER_MINUTE
    edges = np.arange(params.meta.start_s, params.meta.end_s, width)
    buckets: list[list[float]] = [[] for _ in edges]

    for seed in seeds:
        log = runner(params, seed)
        placed = {
            event.order_id: event.t_s
            for event in log
            if event.to_state == State.PLACED and event.order_id
        }
        for event in log:
            if event.to_state != State.READY or event.order_id not in placed:
                continue
            at = placed[event.order_id]
            index = int((at - params.meta.start_s) // width)
            if 0 <= index < len(buckets):
                buckets[index].append(event.t_s - at)

    means, safes = [], []
    for waits in buckets:
        if waits:
            means.append(float(mean(waits)))
            safes.append(float(np.percentile(waits, SAFE_QUANTILE)))
        else:                                   # nobody ordered then, in any run
            means.append(0.0)
            safes.append(0.0)

    return Forecast(
        bin_minutes=bin_minutes,
        opens_at_s=float(params.meta.start_s),
        mean_wait_s=tuple(means),
        safe_wait_s=tuple(safes),
        seeds=len(seeds),
        scenario=params.meta.scenario,
        quantile=SAFE_QUANTILE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Forecast the wait by time of day.")
    # The hybrid, not the free fit. The free fit never saw the morning, because
    # nobody was counting before 10:20, so its early bins hold the flat value its
    # search started from, and the app was quoting 2.8 minutes at 09:00 for a
    # building that lets 520 students out at 09:15. A promise is the one place
    # a placeholder cannot be allowed to stand in for a measurement.
    parser.add_argument("--params", nargs="+",
                        default=["params/base.yaml", "params/observed.yaml",
                                 "params/hybrid_arrivals.yaml"])
    parser.add_argument("--seeds", type=int, default=24)
    parser.add_argument("--bin-minutes", type=float, default=15.0)
    parser.add_argument("--out", default="params/forecast.yaml")
    args = parser.parse_args()

    def runner(params, seed):
        from sim.engine import run
        return run(params, seed).log

    params = load_params(*args.params)
    result = forecast(params, runner, seeds=list(range(args.seeds)),
                      bin_minutes=args.bin_minutes)

    print(f"{result.scenario}, {result.seeds} simulated days")
    print(f"{'from':>7}{'mean':>8}{'p80':>8}")
    print("-" * 24)
    for index, (m, s) in enumerate(zip(result.mean_wait_s, result.safe_wait_s)):
        at = int(result.opens_at_s + index * result.bin_minutes * 60)
        bar = "#" * int(s / 30)
        print(f"  {at//3600:02d}:{at%3600//60:02d}{m/60:>8.1f}{s/60:>8.1f}  {bar}")

    Path(args.out).write_text(
        "# Written by sim/forecast.py. The app reads this to answer 'when will\n"
        "# it be ready' and 'when should I order', so a promise made at the\n"
        "# counter is a prediction the model actually made, and promise_error\n"
        "# can be run over the app's own log afterwards to see whether it held.\n"
        f"# Quoted off the {SAFE_QUANTILE}th percentile, not the mean: a promise\n"
        "# kept on average is missed half the time.\n\n"
        + yaml.safe_dump(to_dict(result), sort_keys=False)
    )
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
