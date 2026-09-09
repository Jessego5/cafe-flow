"""Fit an arrival curve so the simulated queue reproduces the observed one.

The class-block model was invented: five tidy blocks, each arriving in a very
tight window. It puts thirty people through the door in five minutes, which is
why it produced seventeen-minute median waits against a cafe where queues of
eleven cleared in twenty.

Nobody counted arrivals; you cannot, standing in a cafe. What was counted is
the queue, every couple of minutes. So the curve is fitted rather than measured:
raise the rate in bins where the model is short of what was seen, lower it where
it is over, and repeat. Demand rises monotonically with the rate in each bin and
the bins barely interact at this load, so a few passes converge.

The result is `arrivals.model: profile`, which is the same machinery the NYC
transaction log used. There it was read straight off timestamps; here it is
inferred from its consequences.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

import yaml

from analysis.calibrate import _queue_over_time
from analysis.metrics import Interval
from core.params import Params, load_params

__all__ = ["fit_profile", "modelled_queue_by_bin"]

SECONDS_PER_MINUTE = 60.0
DAMPING = 0.6          # move part of the way each pass, or it oscillates
FLOOR = 0.05           # a bin nobody was watching still gets some demand


def modelled_queue_by_bin(
    params: Params, seeds: list[int], bin_minutes: float, runner
) -> dict[int, float]:
    """Mean simulated queue in each bin, read on the cadence a person reads it."""
    width = bin_minutes * SECONDS_PER_MINUTE
    collected: dict[int, list[float]] = {}
    for seed in seeds:
        log = runner(params, seed)
        start = float(params.meta.start_s)
        while start < params.meta.end_s:
            window = Interval(start, min(start + width, float(params.meta.end_s)))
            depths = _queue_over_time(log, window, every_s=120.0)
            if depths:
                collected.setdefault(int(start // 60), []).extend(depths)
            start += width
    return {at: mean(v) for at, v in collected.items()}


def fit_profile(
    observed: dict[int, float],
    base: list[str],
    runner,
    *,
    bin_minutes: float = 30.0,
    seeds: list[int] | None = None,
    passes: int = 6,
) -> tuple[dict, list[tuple[int, float, float]]]:
    """Return a profile overlay and the final bin-by-bin comparison."""
    seeds = seeds or [0, 1, 2, 3, 4, 5]
    params = load_params(*base)
    opens = params.meta.start_s // 60
    closes = params.meta.end_s // 60
    bins = list(range(int(opens), int(closes), int(bin_minutes)))

    # start flat, at a rate the mean observed queue makes plausible
    rate = {at: 20.0 for at in bins}

    for _ in range(passes):
        overlay = _overlay(rate, bins, bin_minutes)
        modelled = modelled_queue_by_bin(
            load_params(*base, overlay=overlay), seeds, bin_minutes, runner
        )
        for at in bins:
            want = observed.get(at)
            if want is None:
                continue                     # nobody watched this bin
            got = modelled.get(at, 0.0)
            ratio = (want + 1.0) / (got + 1.0)          # +1 keeps empty bins finite
            rate[at] = max(FLOOR, rate[at] * (1 + DAMPING * (ratio - 1)))

    overlay = _overlay(rate, bins, bin_minutes)
    modelled = modelled_queue_by_bin(
        load_params(*base, overlay=overlay), seeds, bin_minutes, runner
    )
    comparison = [
        (at, observed[at], modelled.get(at, 0.0)) for at in bins if at in observed
    ]
    return overlay, comparison


def _overlay(rate: dict[int, float], bins: list[int], bin_minutes: float) -> dict:
    return {
        "arrivals": {
            "model": "profile",
            "class_blocks": None,
            "capture_rate": 1.0,
            "background_per_hour": 0.0,
            "profile": {
                "bin_minutes": bin_minutes,
                "rate_per_hour": [round(rate[at], 3) for at in bins],
            },
            "source": "fitted",
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit an arrival curve to an observed queue."
    )
    parser.add_argument("observed", help="json of bin start (minutes) -> mean queue")
    parser.add_argument("--params", nargs="+", default=["params/base.yaml", "params/observed.yaml"])
    parser.add_argument("--out", default="params/fitted_arrivals.yaml")
    parser.add_argument("--seeds", type=int, default=6)
    args = parser.parse_args()

    def runner(params, seed):
        from sim.engine import run
        return run(params, seed).log

    observed = {int(k): float(v) for k, v in json.loads(Path(args.observed).read_text()).items()}
    overlay, comparison = fit_profile(
        observed, args.params, runner, seeds=list(range(args.seeds))
    )

    print(f"{'bin':>7}{'observed':>10}{'modelled':>10}{'rate/h':>9}")
    print("-" * 36)
    rates = overlay["arrivals"]["profile"]["rate_per_hour"]
    opens = load_params(*args.params).meta.start_s // 60
    for at, want, got in comparison:
        index = int((at - opens) // 30)
        print(f"  {at//60:02d}:{at%60:02d}{want:>10.1f}{got:>10.1f}{rates[index]:>9.1f}")
    error = mean(abs(g - w) for _, w, g in comparison)
    print(f"\nmean absolute error {error:.2f} people")

    Path(args.out).write_text(
        "# Fitted by sim/fit_profile.py so the simulated queue reproduces the one\n"
        "# counted in the cafe. Arrivals were never counted -- you cannot count\n"
        "# them standing in a cafe -- so the curve is inferred from its\n"
        "# consequences rather than measured.\n\n"
        + yaml.safe_dump(overlay, sort_keys=False)
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
