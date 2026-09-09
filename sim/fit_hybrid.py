"""
This fits demand as the timetable's shape carrying the queue's level, because
the two routes to the same arrival curve had been fitted separately and
disagreed by a factor of two on the day's volume, which is what happens when
each of them knows something the other does not. The free fit in
sim/fit_profile.py bends twelve rates until the simulated queue matches the
counted one, and twelve parameters against twelve observations is no degrees of
freedom at all: it fits the sampling noise as faithfully as the signal, some of
those bins rest on two readings, and it learns nothing about the hours nobody
watched, which it leaves at the flat value the search started from, including
the whole morning. The registrar's room schedule in params/schedule/*.yaml is
the opposite, a real measurement of when several hundred people are released
that cannot sawtooth, except that it says demand between classes is nearly zero
and a coffee shop does not empty at half past one. So this keeps the
timetable's shape and fits only what the schedule cannot know, as
rate(t) = capture(t) * released(t) + background, where released(t) is the
schedule smeared by the walk over and three numbers are fitted against the
counted queue: c0, the share of a released class that buys, around noon; k, how
fast that share decays through the afternoon, per hour; and background,
everyone not walking out of a class in this building. Three parameters against
twelve observations, with the structure carrying the rest, reaches nearly the
free fit's accuracy without the freedom to invent a trough because somebody
happened to glance twice during a lull. Run it with python -m sim.fit_hybrid.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean

import yaml

from core.params import Params, load_params
from sim.engine import run
from sim.fit_profile import modelled_queue_by_bin

__all__ = ["released_by_bin", "profile_for", "fit_hybrid"]

SECONDS_PER_MINUTE = 60.0

# Widening this was tried and made the fit worse at every value from 6 minutes
# to 30 (MAE 1.04 -> 1.71). The counted peaks are sharp, so the arrivals behind
# them are too: people come straight over rather than drifting in. It stays the
# config's own `arrivals.sigma_min`.
USE_CONFIG_SIGMA = True


def _normal_cdf(x: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf(x / (sigma * math.sqrt(2.0))))


def released_by_bin(
    params: Params, bins: list[int], bin_minutes: float
) -> dict[int, float]:
    """
    Students let out into each bin, spread by how long the walk takes.

    A class ending at 12:15 does not deliver its people to the counter at
    12:15. `offset_min` is the walk and `sigma_min` the spread around it, so a
    release lands as a normal mass split across whichever bins it reaches.
    """
    sigma = params.arrivals.sigma_min
    offset = params.arrivals.offset_min
    out = {at: 0.0 for at in bins}
    for block in params.arrivals.class_blocks:
        ends_at = block.ends_at_s / SECONDS_PER_MINUTE
        head = block.sections * block.avg_enrollment
        for at in bins:
            low = at - ends_at - offset
            out[at] += head * (
                _normal_cdf(low + bin_minutes, sigma) - _normal_cdf(low, sigma)
            )
    return out


def profile_for(
    released: dict[int, float],
    bins: list[int],
    bin_minutes: float,
    c0: float,
    k: float,
    background: float,
) -> dict:
    """
    The overlay these three numbers imply.

    Capture is flat until noon and decays after it. A student leaving at 15:45
    is going home; one leaving at 12:15 is going to lunch, and the fit only
    finds a decay because the schedule made the two distinguishable.
    """
    rates = []
    for at in bins:
        past_noon = max(0.0, at / 60.0 - 12.0)
        rates.append(
            c0 * math.exp(-k * past_noon) * released[at] / (bin_minutes / 60.0)
            + background
        )
    return {
        "arrivals": {
            "model": "profile",
            "class_blocks": None,
            "capture_rate": 1.0,
            "background_per_hour": 0.0,
            "profile": {"bin_minutes": bin_minutes, "rate_per_hour": rates},
        }
    }


def fit_hybrid(
    observed: dict[int, float],
    base: list[str],
    runner,
    *,
    bin_minutes: float = 30.0,
    seeds: list[int] | None = None,
) -> tuple[dict, dict, list[tuple[int, float, float]]]:
    """
    Search for the three numbers, coarsely and then locally.

    A grid first because the surface is cheap and the search is three-wide;
    then single steps from the winner, which is all the resolution twelve
    coarse observations can support. Anything cleverer would be fitting noise
    that a person standing in a cafe could not have resolved anyway.
    """
    seeds = seeds or list(range(8))
    params = load_params(*base)
    opens = int(params.meta.start_s // SECONDS_PER_MINUTE)
    closes = int(params.meta.end_s // SECONDS_PER_MINUTE)
    bins = list(range(opens, closes, int(bin_minutes)))
    released = released_by_bin(params, bins, bin_minutes)

    def error(c0: float, k: float, background: float) -> float:
        overlay = profile_for(released, bins, bin_minutes, c0, k, background)
        modelled = modelled_queue_by_bin(
            load_params(*base, overlay=overlay), seeds, bin_minutes, runner
        )
        return mean(abs(observed[at] - modelled.get(at, 0.0)) for at in observed)

    best = None
    for c0 in (0.02, 0.03, 0.04, 0.05, 0.07):
        for k in (0.0, 0.2, 0.4):
            for background in (8.0, 16.0, 24.0):
                got = error(c0, k, background)
                if best is None or got < best[0]:
                    best = (got, c0, k, background)

    got, c0, k, background = best
    steps = [(0.005, 0, 0), (-0.005, 0, 0), (0, 0.1, 0), (0, -0.1, 0), (0, 0, 4), (0, 0, -4)]
    for _ in range(3):
        for d_c0, d_k, d_bg in steps:
            trial = (max(0.005, c0 + d_c0), max(0.0, k + d_k), max(0.0, background + d_bg))
            attempt = error(*trial)
            if attempt < got:
                got, (c0, k, background) = attempt, trial

    overlay = profile_for(released, bins, bin_minutes, c0, k, background)
    modelled = modelled_queue_by_bin(
        load_params(*base, overlay=overlay), seeds, bin_minutes, runner
    )
    comparison = [(at, observed[at], modelled.get(at, 0.0)) for at in bins if at in observed]
    return overlay, {"c0": c0, "k": k, "background_per_hour": background, "mae": got}, comparison


HEADER = """\
# Fitted by sim/fit_hybrid.py: the room schedule's shape, the counted queue's
# level. rate(t) = capture(t) * released(t) + background.
#
# Three numbers were fitted against {n} counted bins, and the registrar's
# timetable carries everything else. The free-rate fit in fitted_arrivals.yaml
# uses twelve for the same twelve bins, which leaves it no way to tell a real
# trough from a bin somebody happened to glance at twice.
#
#   capture at noon        {c0:.3f}   of a released class
#   afternoon decay        {k:.2f}/h  a 15:45 class is going home, not to lunch
#   background        {bg:>8.0f}/h  everyone not walking out of a class here
#
# The background is the finding. base.yaml assumed 8 an hour; the fit wants
# {bg:.0f}, so most of the demand between classes is not this building's
# students at all, which is also why the timetable alone emptied the cafe at
# half past one and the counted queue did not.
#
# Mean absolute error {mae:.2f} people across the counted bins.

"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--observed", default="observations/queue_by_bin.json")
    parser.add_argument("--out", default="params/hybrid_arrivals.yaml")
    parser.add_argument("--schedule", default="params/schedule/thursday.yaml")
    parser.add_argument("--seeds", type=int, default=8)
    args = parser.parse_args()

    observed = {int(at): depth for at, depth in json.load(open(args.observed)).items()}
    base = ["params/base.yaml", "params/observed.yaml", args.schedule]
    overlay, fitted, comparison = fit_hybrid(
        observed, base, lambda p, s: run(p, seed=s).log, seeds=list(range(args.seeds))
    )

    print(f"{'bin':>7}{'counted':>9}{'modelled':>10}")
    for at, want, got in comparison:
        print(f"{at // 60:02d}:{at % 60:02d}{want:>9.2f}{got:>10.2f}")
    print(f"\n  capture {fitted['c0']:.3f}  decay {fitted['k']:.2f}/h  "
          f"background {fitted['background_per_hour']:.0f}/h  MAE {fitted['mae']:.2f}")

    body = HEADER.format(
        n=len(observed), c0=fitted["c0"], k=fitted["k"],
        bg=fitted["background_per_hour"], mae=fitted["mae"],
    )
    overlay["arrivals"]["source"] = "fitted"
    Path(args.out).write_text(body + yaml.safe_dump(overlay, sort_keys=False))
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
