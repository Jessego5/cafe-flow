"""Turn an afternoon's counting into parameters, and say how well it fits.

M8, in the shape the observation actually takes. The plan assumed CSVs sampled
every thirty seconds; what one person watching one rush can honestly produce is
five numbers. So this reads the summary the counter hands you and does the two
things that matter: writes down what was counted, and searches for the one
parameter that cannot be counted directly.

`analysis/` may not depend on either runtime, so the thing that runs a day is
passed in. That keeps the arithmetic here testable without a simulator, and it
means a calibration could just as well be driven from a real day's log. Only
`main()`, which is a composition point rather than a calculation, reaches for
`sim`.

**The gate matters more than the fit.** If the calibrated model cannot
reproduce what was seen, the model is wrong, and the answer is to fix the model
rather than tune the fit. Fitting `capture_rate` to match a volume is
legitimate; fitting it to paper over a wrong resource model produces a
confident wrong answer.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Callable

import yaml

from analysis.metrics import balk_count_and_lost_margin, busiest_window
from core.events import EventLog
from core.params import ConfigError, Params, load_params

#: Runs one day and hands back its log. Injected so `analysis/` stays free of
#: both runtimes (ground rule 2).
Runner = Callable[[Params, int], EventLog]

__all__ = ["Observation", "read_summary", "to_overlay", "fit_capture_rate", "validate"]

#: Nothing counted at a counter is exact. A fit that lands inside this of the
#: observed volume has done its job; chasing further would be fitting noise.
VOLUME_TOLERANCE = 0.10
SEARCH_STEPS = 24


@dataclass
class Observation:
    """What one person saw in one rush."""

    where: str = "Ground Truth"
    on: str = ""
    minutes: float = 0.0
    orders: int = 0
    iced: int = 0
    food: int = 0
    walked_out: int = 0
    longest_line: int = 0
    press_seconds: list[float] = field(default_factory=list)
    press_size: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def orders_per_hour(self) -> float:
        if not self.minutes:
            raise ConfigError("the observation records no elapsed time")
        return self.orders * 60.0 / self.minutes

    @property
    def iced_share(self) -> float | None:
        return self.iced / self.orders if self.orders and self.iced else None

    @property
    def food_share(self) -> float | None:
        return self.food / self.orders if self.orders and self.food else None

    @property
    def balks_per_hour(self) -> float | None:
        if not self.minutes:
            return None
        return self.walked_out * 60.0 / self.minutes

    @property
    def press_mean_s(self) -> float | None:
        return mean(self.press_seconds) if self.press_seconds else None


_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"


def read_summary(text: str) -> Observation:
    """Parse what the counter produces. Missing lines are simply not observed.

    Deliberately forgiving: this is read off a phone and pasted into a message,
    and refusing an afternoon's work over a stray character would be absurd.
    """
    seen = Observation()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        header = re.match(r"^(.+?),\s*(\d{4}-\d{2}-\d{2})$", line)
        if header:
            seen.where, seen.on = header.group(1).strip(), header.group(2)
            continue

        if match := re.match(rf"^watched:\s*{_NUMBER}", line):
            seen.minutes = float(match.group(1))
        elif match := re.match(rf"^orders:\s*{_NUMBER}", line):
            seen.orders = int(float(match.group(1)))
        elif match := re.match(rf"^iced:\s*{_NUMBER}", line):
            seen.iced = int(float(match.group(1)))
        elif match := re.match(rf"^with food:\s*{_NUMBER}", line):
            seen.food = int(float(match.group(1)))
        elif match := re.match(rf"^walked out:\s*{_NUMBER}", line):
            seen.walked_out = int(float(match.group(1)))
        elif match := re.match(rf"^longest line:\s*{_NUMBER}", line):
            seen.longest_line = int(float(match.group(1)))
        elif line.startswith("press:"):
            # the counter prints the cycles, then an average in brackets; the
            # average is not a fourth cycle
            cycles = line.split("(", 1)[0]
            seen.press_seconds = [float(value) for value in re.findall(rf"{_NUMBER}s", cycles)]
            if size := re.search(rf"{_NUMBER} in at a time", line):
                seen.press_size = int(float(size.group(1)))
        else:
            seen.notes.append(line)
    return seen


def _rescaled_mix(params: Params, food_share: float) -> dict[str, float]:
    """Move the food/drink split to what was counted, keeping the shape within
    each group. Nobody counted individual drinks, so their relative shares stay
    as they were."""
    food = {
        name for name, spec in params.menu.items()
        if any(task.station in ("panini_press", "food_counter") for task in spec.tasks)
    }
    if not food or food == set(params.mix.drink):
        raise ConfigError("cannot rescale the mix: no food, or nothing but food")

    current_food = sum(share for name, share in params.mix.drink.items() if name in food)
    current_drink = 1.0 - current_food
    if current_food <= 0 or current_drink <= 0:
        raise ConfigError("the mix already has no food or no drinks")

    out: dict[str, float] = {}
    for name, share in params.mix.drink.items():
        scale = (
            food_share / current_food if name in food
            else (1.0 - food_share) / current_drink
        )
        out[name] = round(share * scale, 9)

    # Rounding has to land exactly on one or validation refuses the file, so the
    # remainder goes onto the largest share, where it is least visible. Done
    # after rounding, not before, or it simply comes back.
    largest = max(out, key=out.get)
    out[largest] += 1.0 - sum(out.values())
    return out


def to_overlay(seen: Observation, params: Params) -> dict:
    """The observed parameters, ready to be written as `params/observed.yaml`.

    Direct counts are `observed`. `capture_rate` is `fitted`, because it is
    searched for rather than seen.
    """
    overlay: dict = {"meta": {"scenario": "ground_truth_observed", "source": "assumed"}}

    if seen.iced_share is not None:
        overlay["mix"] = {
            "serve": {
                "hot": round(1.0 - seen.iced_share, 4),
                "iced": round(seen.iced_share, 4),
            },
            "source_of": {"serve": "observed"},
        }

    if seen.food_share is not None:
        overlay.setdefault("mix", {})
        overlay["mix"]["drink"] = _rescaled_mix(params, seen.food_share)
        overlay["mix"].setdefault("source_of", {})["drink"] = "observed"

    press: dict = {}
    if seen.press_mean_s is not None:
        press["run_s"] = round(seen.press_mean_s, 1)
    if seen.press_size:
        press["batch_size"] = seen.press_size
    if press:
        press["source_of"] = {key: "observed" for key in press}
        overlay["stations"] = {"panini_press": press}

    return overlay


def _peak_orders_per_hour(params: Params, seeds: list[int], runner: Runner) -> float:
    """What the model thinks the busiest hour looks like."""
    rates: list[float] = []
    for seed in seeds:
        log = runner(params, seed)
        window = busiest_window(log)
        if window is None:
            rates.append(0.0)
            continue
        placed = [
            event for event in log
            if event.to_state == "placed" and window.holds(event.t_s)
        ]
        rates.append(len(placed) * 3600.0 / window.length_s)
    return mean(rates)


def fit_capture_rate(
    seen: Observation,
    base: list[str],
    overlay: dict,
    runner: Runner,
    *,
    seeds: list[int] | None = None,
) -> tuple[float, float]:
    """Search for the capture rate that reproduces the volume that was counted.

    Bisection rather than anything cleverer: demand rises monotonically with
    the capture rate, the observation is one coarse number, and a smarter
    search would only be fitting its noise.

    Returns the rate and the peak orders an hour it produces.
    """
    seeds = seeds or [0, 1, 2, 3]
    target = seen.orders_per_hour

    def rate_at(capture: float) -> float:
        merged = dict(overlay)
        merged.setdefault("arrivals", {})["capture_rate"] = capture
        merged["arrivals"].setdefault("source_of", {})["capture_rate"] = "fitted"
        return _peak_orders_per_hour(load_params(*base, overlay=merged), seeds, runner)

    low, high = 0.001, 1.0
    if rate_at(high) < target:
        raise ConfigError(
            f"even capturing everyone gives {rate_at(high):.0f} orders an hour at peak, "
            f"short of the {target:.0f} counted. The class blocks are too small: fix "
            f"arrivals.class_blocks before fitting."
        )

    best = low
    for _ in range(SEARCH_STEPS):
        best = (low + high) / 2
        produced = rate_at(best)
        if abs(produced - target) / target <= VOLUME_TOLERANCE / 4:
            break
        if produced < target:
            low = best
        else:
            high = best
    return round(best, 5), rate_at(best)


@dataclass
class Validation:
    """Whether the calibrated model reproduces what was actually seen."""

    observed_per_hour: float
    modelled_per_hour: float
    observed_balks_per_hour: float | None
    modelled_balks_per_hour: float | None
    observed_longest_line: int
    provenance: str

    @property
    def volume_error(self) -> float:
        if not self.observed_per_hour:
            return 0.0
        return (self.modelled_per_hour - self.observed_per_hour) / self.observed_per_hour

    @property
    def passes(self) -> bool:
        return abs(self.volume_error) <= VOLUME_TOLERANCE

    @property
    def balk_ratio(self) -> float | None:
        """How far out the model's patience is.

        Volume is what `capture_rate` is fitted to, so it agreeing proves
        little. Balking is not fitted to anything, which makes it the honest
        test: the model predicts how many people give up on the queue, and
        somebody stood there and counted them.
        """
        if not self.observed_balks_per_hour or self.modelled_balks_per_hour is None:
            return None
        return self.modelled_balks_per_hour / self.observed_balks_per_hour

    def render(self) -> str:
        lines = [
            f"  volume   observed {self.observed_per_hour:5.0f}/h   "
            f"modelled {self.modelled_per_hour:5.0f}/h   "
            f"error {self.volume_error:+.0%}",
        ]
        if self.observed_balks_per_hour is not None:
            lines.append(
                f"  balks    observed {self.observed_balks_per_hour:5.0f}/h   "
                f"modelled {self.modelled_balks_per_hour:5.0f}/h"
            )
        lines.append(f"  {self.provenance}")
        lines.append(
            "  volume PASSES: the model reproduces what was counted"
            if self.passes
            else "  volume FAILS: fix the model, do not tune the fit"
        )

        ratio = self.balk_ratio
        if ratio is not None and not 0.5 <= ratio <= 2.0:
            direction = "impatient" if ratio > 1 else "patient"
            lines += [
                "",
                f"  Balking is {ratio:.1f}x off, which is the number worth arguing with.",
                "  Volume is what capture_rate was fitted to, so it agreeing proves",
                "  little. Nothing was fitted to balks. Modelling customers as too",
                f"  {direction} means customers.balk_tolerance_min is wrong, and it is the",
                "  softest assumption left in the file.",
            ]
        return "\n".join(lines)


def validate(
    seen: Observation,
    params: Params,
    runner: Runner,
    seeds: list[int] | None = None,
) -> Validation:
    seeds = seeds or [0, 1, 2, 3]
    modelled = _peak_orders_per_hour(params, seeds, runner)

    balks: list[float] = []
    for seed in seeds:
        log = runner(params, seed)
        window = busiest_window(log)
        losses = balk_count_and_lost_margin(log, window=window)
        if window is not None:
            balks.append(losses["balked"] * 3600.0 / window.length_s)

    return Validation(
        observed_per_hour=seen.orders_per_hour,
        modelled_per_hour=modelled,
        observed_balks_per_hour=seen.balks_per_hour,
        modelled_balks_per_hour=mean(balks) if balks else None,
        observed_longest_line=seen.longest_line,
        provenance=params.provenance_report().detail(),
    )


def main() -> None:
    # The one place this module composes with a runtime.
    from sim.engine import run as run_day

    def runner(params: Params, seed: int) -> EventLog:
        return run_day(params, seed).log

    parser = argparse.ArgumentParser(
        description="Turn a counted rush into params/observed.yaml."
    )
    parser.add_argument("summary", help="the file the counter's summary was pasted into")
    parser.add_argument("--params", nargs="+", default=["params/base.yaml"])
    parser.add_argument("--out", default="params/observed.yaml")
    parser.add_argument("--seeds", type=int, default=4)
    args = parser.parse_args()

    seen = read_summary(Path(args.summary).read_text())
    base = load_params(*args.params)
    print(
        f"{seen.where} {seen.on}: {seen.orders} orders in {seen.minutes:.0f} min "
        f"= {seen.orders_per_hour:.0f}/h at peak"
    )

    overlay = to_overlay(seen, base)
    seeds = list(range(args.seeds))
    capture, produced = fit_capture_rate(seen, args.params, overlay, runner, seeds=seeds)
    overlay.setdefault("arrivals", {})["capture_rate"] = capture
    overlay["arrivals"].setdefault("source_of", {})["capture_rate"] = "fitted"
    print(f"fitted capture_rate {capture} -> {produced:.0f}/h at peak")

    calibrated = load_params(*args.params, overlay=overlay)
    report = validate(seen, calibrated, runner, seeds)
    print(report.render())

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Written by analysis/calibrate.py from a counted rush.\n"
        f"# {seen.where}, {seen.on}: {seen.orders} orders in {seen.minutes:.0f} minutes.\n"
        "# Direct counts are observed; capture_rate is fitted.\n\n"
        + yaml.safe_dump(overlay, sort_keys=False)
    )
    print(f"wrote {target}")
    raise SystemExit(0 if report.passes else 1)


if __name__ == "__main__":
    main()
