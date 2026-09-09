"""Turn a session's counting into parameters, and say how well it fits.

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
from core.events import EventLog, EventType
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
    balk_lines: list[int] = field(default_factory=list)
    line_samples: list[int] = field(default_factory=list)

    # clock times, where the observation carried them: "10:48 3" rather than a
    # bare depth. Knowing when a reading was taken means it can be compared
    # against the same time of day in the model rather than against its
    # busiest hour, whenever that happened to fall.
    sample_times: list[str] = field(default_factory=list)
    balk_times: list[str] = field(default_factory=list)
    watched_from: str = ""
    watched_to: str = ""
    press_seconds: list[float] = field(default_factory=list)
    press_size: int = 0

    # what the photographs could not settle
    machine: str = ""
    holds: int = 0
    cashier: str = ""
    baristas: int = 0
    opens: str = ""
    closes: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def typical_line(self) -> float | None:
        """The queue as it actually was, sampled on a timer.

        Unbiased, unlike the depth recorded when somebody balked: that only
        ever reads the queue when it was long enough that someone left.
        """
        return mean(self.line_samples) if self.line_samples else None

    @property
    def busiest_line(self) -> int | None:
        return max(self.line_samples) if self.line_samples else None

    @property
    def typical_balk_line(self) -> float | None:
        """How deep the queue was when people gave up. This is what identifies
        the tolerance; the rate alone only says how many left."""
        return mean(self.balk_lines) if self.balk_lines else None

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
    and refusing an hour's work over a stray character would be absurd.
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

        if line.startswith("watched:"):
            body = line.split(":", 1)[1]
            if span := re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", line):
                seen.watched_from, seen.watched_to = span.group(1), span.group(2)
            if match := re.search(rf"{_NUMBER}\s*min", body):
                seen.minutes = float(match.group(1))
        elif match := re.match(rf"^orders:\s*{_NUMBER}", line):
            seen.orders = int(float(match.group(1)))
        elif match := re.match(rf"^iced:\s*{_NUMBER}", line):
            seen.iced = int(float(match.group(1)))
        elif match := re.match(rf"^with food:\s*{_NUMBER}", line):
            seen.food = int(float(match.group(1)))
        elif match := re.match(rf"^walked out:\s*{_NUMBER}", line):
            seen.walked_out = int(float(match.group(1)))
            stamped = re.findall(r"(\d{1,2}:\d{2})\s+at\s+(\d+)", line)
            if stamped:
                seen.balk_times = [at for at, _ in stamped]
                seen.balk_lines = [int(depth) for _, depth in stamped]
            elif depths := re.search(r"line was ([\d,\s]+)", line):
                seen.balk_lines = [
                    int(value) for value in re.findall(r"\d+", depths.group(1))
                ]
        elif line.startswith("line every"):
            body = line.split(":", 1)[1] if ":" in line else ""
            stamped = re.findall(r"(\d{1,2}:\d{2})\s+(\d+)", line)
            if stamped:
                seen.sample_times = [at for at, _ in stamped]
                seen.line_samples = [int(depth) for _, depth in stamped]
            else:
                seen.line_samples = [int(v) for v in re.findall(r"\d+", body)]
        elif line.startswith("food machine:"):
            body = line.split(":", 1)[1]
            seen.machine = body.split(",")[0].strip()
            if holds := re.search(rf"holds\s*{_NUMBER}", body):
                seen.holds = int(float(holds.group(1)))
        elif line.startswith("till person makes drinks:"):
            seen.cashier = line.split(":", 1)[1].strip()
        elif match := re.match(rf"^baristas at peak:\s*{_NUMBER}", line):
            seen.baristas = int(float(match.group(1)))
        elif line.startswith("hours:"):
            body = line.split(":", 1)[1].strip()
            if "-" in body:
                seen.opens, _, seen.closes = (part.strip() for part in body.partition("-"))
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


#: What each answer implies for the station that heats the food. A press holds
#: two side by side; anything that heats a pre-made item holds one, which is
#: what removes the batching. Times are conventional for the machine class and
#: stay `published`; the class itself is what was observed.
MACHINES: dict[str, tuple[float, int]] = {
    "microwave": (60.0, 1),
    "high-speed oven": (45.0, 1),
    "panini press": (240.0, 2),
    "contact grill": (240.0, 2),
}


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
    if seen.press_mean_s is not None:                     # somebody timed it
        press["run_s"] = round(seen.press_mean_s, 1)
        press["source_of"] = {"run_s": "observed"}
    elif seen.machine in MACHINES:                        # or just said what it is
        run_s, holds = MACHINES[seen.machine]
        press["run_s"] = run_s
        press["batch_size"] = seen.holds or holds
        press["source_of"] = {"run_s": "published", "batch_size": "observed"}
    if seen.press_size:
        press["batch_size"] = seen.press_size
        press.setdefault("source_of", {})["batch_size"] = "observed"
    if press:
        overlay["stations"] = {"panini_press": press}

    if seen.baristas:
        opens = seen.opens or params.meta.sim_start
        closes = seen.closes or params.meta.sim_end
        overlay["staffing"] = [
            {"from": opens, "to": closes, "baristas": seen.baristas, "source": "observed"}
        ]
    if seen.opens or seen.closes:
        overlay["meta"] = {
            **overlay.get("meta", {}),
            "sim_start": seen.opens or params.meta.sim_start,
            "sim_end": seen.closes or params.meta.sim_end,
            "source_of": {"sim_start": "observed", "sim_end": "observed"},
        }

    return overlay


def open_questions(seen: Observation) -> list[str]:
    """What the observation raises that a config change cannot answer.

    A dedicated cashier is not a parameter: `serve()` always takes a barista
    for the register, so the shared assumption is in the code and not only in
    the YAML. Saying so is better than silently modelling something else.
    """
    asks: list[str] = []
    if seen.cashier in ("never",):
        asks.append(
            "The till person never makes drinks, so the register does not compete "
            "for barista time. That is an engine change, not a config one: serve() "
            "always takes a barista for the register phase."
        )
    if seen.machine and seen.machine not in MACHINES:
        asks.append(f"No timings on file for a {seen.machine!r}; it stays assumed.")
    if seen.walked_out and not seen.balk_lines:
        asks.append(
            "Balks were counted without a queue depth, so the rate can be checked "
            "but the tolerance cannot be fitted."
        )
    return asks


def _typical_queue(params: Params, seeds: list[int], runner: Runner) -> float:
    """The simulated queue, averaged over the rush and over seeds."""
    depths: list[float] = []
    for seed in seeds:
        log = runner(params, seed)
        window = busiest_window(log)
        if window is not None:
            depths.extend(_queue_over_time(log, window))
    return mean(depths) if depths else 0.0


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
    """Search for the capture rate that reproduces what was counted.

    Against volume where somebody counted orders, and against queue depth
    otherwise, which is what the original plan fitted to, and what a person
    watching a rush can actually produce. Either way one number is being
    matched by one knob.

    Bisection rather than anything cleverer: demand rises monotonically with
    the capture rate, the observation is coarse, and a smarter search would
    only be fitting its noise.

    Returns the rate and the value it produces, in whichever measure was used.
    """
    seeds = seeds or [0, 1, 2, 3]

    if seen.orders and seen.minutes:
        target, measure = seen.orders_per_hour, _peak_orders_per_hour
    elif seen.typical_line is not None:
        target, measure = seen.typical_line, _typical_queue
    else:
        raise ConfigError(
            "nothing to fit against: the observation records neither an order "
            "count over a known time nor any queue depth. One or the other is "
            "what pins the capture rate."
        )

    def rate_at(capture: float) -> float:
        merged = dict(overlay)
        merged.setdefault("arrivals", {})["capture_rate"] = capture
        merged["arrivals"].setdefault("source_of", {})["capture_rate"] = "fitted"
        return measure(load_params(*base, overlay=merged), seeds, runner)

    low, high = 0.001, 1.0
    if rate_at(high) < target:
        raise ConfigError(
            f"even capturing everyone reaches {rate_at(high):.1f} against the "
            f"{target:.1f} counted. The class blocks are too small to supply that "
            f"demand: fix arrivals.class_blocks before fitting."
        )

    # Keep the best candidate seen rather than whatever midpoint the loop ends
    # on. Each evaluation is an average over a handful of simulated days, so it
    # carries noise, and one misstep on a noisy objective sends bisection the
    # wrong way with no route back.
    best, best_produced, best_error = low, rate_at(low), float("inf")
    for _ in range(SEARCH_STEPS):
        middle = (low + high) / 2
        produced = rate_at(middle)
        error = abs(produced - target) / target

        if error < best_error:
            best, best_produced, best_error = middle, produced, error
        if error <= VOLUME_TOLERANCE / 4:
            break
        if produced < target:
            low = middle
        else:
            high = middle

    return round(best, 5), best_produced


def _queue_over_time(log, window, every_s: float = 60.0) -> list[float]:
    """The simulated queue, read on the same cadence a person reads it.

    Membership, not a running tally: an order is in the queue from the moment
    it is placed until it reaches the shelf or the customer gives up. Counting
    a transition into each waiting state would add the same order three times
    on its way through and the depth would only ever climb.
    """
    from core.states import State

    leaves = {
        str(State.READY), str(State.PICKED_UP), str(State.BALKED),
        str(State.ABANDONED), str(State.CANCELLED),
    }
    # Ordered by the log's own sequence, not by the state's name. A balk is
    # placed and abandoned at the same instant, and sorting those two by string
    # puts "balked" before "placed", removing the order before it was added,
    # so it stayed in the queue for the rest of the day and every balk inflated
    # the measurement.
    moves = sorted(
        (event.t_s, event.seq, event.order_id, event.to_state)
        for event in log
        if event.type is EventType.STATE_CHANGE
        and event.order_id is not None
        and event.to_state is not None
    )

    waiting: set[str] = set()
    index, out = 0, []
    at = window.start_s
    while at < window.end_s:
        while index < len(moves) and moves[index][0] <= at:
            _, _, order_id, to_state = moves[index]
            if to_state == str(State.PLACED):
                waiting.add(order_id)
            elif to_state in leaves:
                waiting.discard(order_id)
            index += 1
        out.append(float(len(waiting)))
        at += every_s
    return out


@dataclass
class Validation:
    """Whether the calibrated model reproduces what was actually seen."""

    observed_per_hour: float
    modelled_per_hour: float
    observed_balks_per_hour: float | None
    modelled_balks_per_hour: float | None
    observed_longest_line: int
    observed_balk_line: float | None
    modelled_balk_line: float | None
    observed_line: float | None
    modelled_line: float | None
    provenance: str

    @property
    def volume_error(self) -> float:
        if not self.observed_per_hour:
            return 0.0
        return (self.modelled_per_hour - self.observed_per_hour) / self.observed_per_hour

    @property
    def queue_error(self) -> float | None:
        if not self.observed_line or self.modelled_line is None:
            return None
        return (self.modelled_line - self.observed_line) / self.observed_line

    @property
    def fitted_against(self) -> str:
        """Whichever measure the capture rate was matched to. The other checks
        are worth more precisely because nothing was fitted to them."""
        return "volume" if self.observed_per_hour else "queue"

    @property
    def passes(self) -> bool:
        error = self.volume_error if self.fitted_against == "volume" else self.queue_error
        return error is not None and abs(error) <= VOLUME_TOLERANCE

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
        lines = []
        if self.observed_per_hour:
            lines.append(
                f"  volume   observed {self.observed_per_hour:5.0f}/h   "
                f"modelled {self.modelled_per_hour:5.0f}/h   "
                f"error {self.volume_error:+.0%}"
            )
        if self.observed_balks_per_hour is not None:
            lines.append(
                f"  balks    observed {self.observed_balks_per_hour:5.0f}/h   "
                f"modelled {self.modelled_balks_per_hour:5.0f}/h"
            )
        if self.observed_line is not None and self.modelled_line is not None:
            error = self.queue_error
            lines.append(
                f"  queue    observed {self.observed_line:5.1f} deep  "
                f"modelled {self.modelled_line:5.1f} deep  "
                + (f"error {error:+.0%}" if error is not None else "")
            )
        if self.observed_balk_line is not None and self.modelled_balk_line is not None:
            lines.append(
                f"  gave up  observed {self.observed_balk_line:5.1f} deep  "
                f"modelled {self.modelled_balk_line:5.1f} deep"
            )
        lines.append(f"  {self.provenance}")
        lines.append(
            f"  {self.fitted_against} PASSES: the model reproduces what was counted"
            if self.passes
            else f"  {self.fitted_against} FAILS: fix the model, do not tune the fit"
        )

        ratio = self.balk_ratio
        if ratio is not None and not 0.5 <= ratio <= 2.0:
            direction = "impatient" if ratio > 1 else "patient"
            lines += [
                "",
                f"  Balking is {ratio:.1f}x off, which is the number worth arguing with.",
                f"  {self.fitted_against.title()} is what capture_rate was turned to match,",
                "  so it agreeing proves little. Nothing is fitted to balking. Modelling",
                f"  customers as too {direction} means customers.balk_tolerance_min is",
                "  wrong, and it is the softest assumption left in the file.",
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

    depths: list[float] = []
    queue: list[float] = []
    for seed in seeds:
        log = runner(params, seed)
        for event in log:
            if event.to_state == "balked" and "queue_depth" in event.payload:
                depths.append(float(event.payload["queue_depth"]))
        window = busiest_window(log)
        if window is not None:
            queue.extend(_queue_over_time(log, window))

    return Validation(
        observed_balk_line=seen.typical_balk_line,
        modelled_balk_line=mean(depths) if depths else None,
        observed_line=seen.typical_line,
        modelled_line=mean(queue) if queue else None,
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
        f"{' '.join(filter(None, (seen.where, seen.on)))}: "
        f"{seen.orders} orders in {seen.minutes:.0f} min "
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
        f"# {', '.join(filter(None, (seen.where, seen.on)))}: "
        f"{seen.orders} orders in {seen.minutes:.0f} minutes.\n"
        "# Direct counts are observed; capture_rate is fitted.\n\n"
        + yaml.safe_dump(overlay, sort_keys=False)
    )
    print(f"wrote {target}")
    raise SystemExit(0 if report.passes else 1)


if __name__ == "__main__":
    main()
