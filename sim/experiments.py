"""Sweep and comparison runner. In-process only.

The experiments cannot go through HTTP: an arm is hundreds of orders and a
sweep is hundreds of arms, and the DES runs a simulated day in milliseconds
because virtual time jumps event to event.

Today this runs named arms — a set of parameter overlays — across seeds and
reports the difference with a confidence interval. M7 adds the parameter sweeps
on top of the same machinery.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
from statistics import mean, stdev

from analysis.metrics import (
    balk_count_and_lost_margin,
    busiest_window,
    crew_utilisation,
    peak_throughput,
    state_counts,
    station_utilisation,
    wait_percentiles,
)
from core.params import Params, load_params, overlay_for
from sim.engine import run

__all__ = ["Arm", "ARMS", "SweepPoint", "run_arm", "compare", "sweep", "render_sweep"]

BASE = "params/base.yaml"
CONFIDENCE_95 = 1.96


@dataclass(frozen=True, slots=True)
class Arm:
    """One configuration to run, as the overlays that produce it."""

    name: str
    overlays: tuple[str, ...] = ()
    note: str = ""

    def params(self, base: str = BASE, overlay: dict | None = None) -> Params:
        return load_params(base, *self.overlays, overlay=overlay)


#: The cafe as it was actually measured, and the base of every arm below.
#:
#: The second file is the one that was missing. `observed.yaml` fits a single
#: `capture_rate` on top of the *invented* class timetable in `base.yaml`;
#: `fitted_arrivals.yaml` replaces that timetable with a curve fitted to the
#: queue counted on 2026-09-03. Without it every experiment here ran on a
#: fabricated demand shape while `sim/forecast.py` -- and therefore every
#: promise the app quotes a customer -- ran on the fitted one. The analysis and
#: the product disagreed about what day it was.
OBSERVED: tuple[str, ...] = (
    "params/observed.yaml",
    "params/fitted_arrivals.yaml",
)

#: The other route to the same curve: the registrar's room schedule rather than
#: a fit to the counted queue. Its own arm rather than a replacement, because
#: the two routes agreeing is evidence and disagreeing is a finding -- and
#: because only this one has content the queue counts did not put there.
#:
#: Thursday: it is the weekday both logged observations fall on. The other four
#: are written out beside it and none of them is loaded by anything yet.
TIMETABLE: tuple[str, ...] = (
    "params/observed.yaml",
    "params/schedule/thursday.yaml",
)

#: Both at once, which is what the disagreement between them turned out to be
#: asking for: the schedule supplies the shape, and three fitted numbers supply
#: the level the schedule cannot know. See `sim/fit_hybrid.py`.
HYBRID: tuple[str, ...] = (
    "params/observed.yaml",
    "params/hybrid_arrivals.yaml",
)


#: The two readings of the espresso bar. Both are assumed until the machine is
#: identified; running them side by side costs the question before answering it.
ARMS: dict[str, Arm] = {
    "manual_bar": Arm(
        "manual_bar",
        OBSERVED,
        "pitcher and separate group head; milk drinks can be batched",
    ),
    "superauto": Arm(
        "superauto",
        OBSERVED + ("params/experiments/superauto.yaml",),
        "one Schaerer super-automatic; no pitcher, so nothing batches",
    ),
    "adoption": Arm(
        "adoption",
        OBSERVED + ("params/experiments/adoption.yaml",),
        "30% order ahead, so that share never sees the line",
    ),
    "adoption_batched": Arm(
        "adoption_batched",
        OBSERVED + ("params/experiments/adoption.yaml", "params/experiments/batch.yaml"),
        "both: ordering ahead and running compatible work together",
    ),
    "reordered": Arm(
        "reordered",
        OBSERVED + ("params/experiments/reorder.yaml",),
        "batching plus a bounded reorder that avoids changeovers, guarded",
    ),
    "microwave": Arm(
        "microwave",
        OBSERVED + ("params/experiments/microwave.yaml",),
        "food pre-made and heated to order, one at a time",
    ),
    "microwave_batched": Arm(
        "microwave_batched",
        OBSERVED + ("params/experiments/microwave.yaml", "params/experiments/batch.yaml"),
        "the same, with batching on — which now has only the wand to work with",
    ),
    "shown_wait": Arm(
        "shown_wait",
        OBSERVED + ("params/experiments/shown_wait.yaml",),
        "the wait on a screen; some people come back twenty minutes later",
    ),
    "second_till": Arm(
        "second_till",
        OBSERVED + ("params/experiments/second_till.yaml",),
        "a second register open at the peaks",
    ),
    "observed": Arm(
        "observed",
        OBSERVED,
        "the cafe as it was measured on 2026-09-03",
    ),
    "timetable": Arm(
        "timetable",
        TIMETABLE,
        "the same cafe, demand built from the room schedule instead of the fit",
    ),
    "hybrid": Arm(
        "hybrid",
        HYBRID,
        "the schedule's shape and the queue's level, three fitted numbers",
    ),
    "fixed_release": Arm(
        "fixed_release",
        HYBRID + ("params/experiments/fixed_release.yaml",),
        "half order ahead, a class block early, whatever the line is doing",
    ),
    "retime": Arm(
        "retime",
        HYBRID + ("params/experiments/retime.yaml",),
        "the planner shows what each slot costs, so some demand moves off the peak",
    ),
    "adaptive_release": Arm(
        "adaptive_release",
        HYBRID + ("params/experiments/adaptive_release.yaml",),
        "the app holds the paid order and joins the queue at the last safe moment",
    ),
    "batched": Arm(
        "batched",
        OBSERVED + ("params/experiments/batch.yaml",),
        "manual bar, running compatible work together at every station that can",
    ),
    "superauto_batched": Arm(
        "superauto_batched",
        OBSERVED + ("params/experiments/superauto.yaml", "params/experiments/batch.yaml"),
        "super-automatic, so only the press has anything left to batch",
    ),
}


@dataclass
class ArmResult:
    arm: Arm
    params: Params
    rows: list[dict] = field(default_factory=list)

    def series(self, key: str) -> list[float]:
        return [row[key] for row in self.rows if row.get(key) is not None]

    def summary(self, key: str) -> tuple[float, float]:
        """Mean and half-width of the 95% interval across seeds."""
        values = self.series(key)
        if not values:
            return (float("nan"), float("nan"))
        if len(values) < 2:
            return (values[0], 0.0)
        half = CONFIDENCE_95 * stdev(values) / len(values) ** 0.5
        return (mean(values), half)


def run_arm(
    arm: Arm, seeds: list[int], *, base: str = BASE, overlay: dict | None = None
) -> ArmResult:
    """Run one arm across seeds, measuring the rush the log itself identifies."""
    params = arm.params(base, overlay)
    result = ArmResult(arm=arm, params=params)

    for seed in seeds:
        day = run(params, seed, scenario=arm.name)
        log = day.log
        window = busiest_window(log)
        waits = wait_percentiles(log, window=window)
        # split out, because an arm that moves people between channels changes
        # who the aggregate is averaging over
        by_channel = wait_percentiles(log, window=window, by_channel=True)
        walkup = by_channel.get("walkup", {})
        utilisation = station_utilisation(log, params, window=window)
        counts = state_counts(log)
        losses = balk_count_and_lost_margin(log)

        result.rows.append(
            {
                "seed": seed,
                "orders": counts.get("placed", 0),
                "served": counts.get("picked_up", 0),
                "in_flight": counts.get("in_flight", 0),
                "balked": losses["balked"],
                "abandoned": losses["abandoned"],
                "lost_fraction": losses["lost_fraction"],
                "lost_margin_cents": losses["lost_margin_cents"],
                "captured_margin_cents": losses["captured_margin_cents"],
                "peak_from_s": window.start_s if window else None,
                "wait_p50_s": waits["p50"],
                "wait_p90_s": waits["p90"],
                "wait_p95_s": waits["p95"],
                "wait_p50_walkup_s": walkup.get("p50"),
                "wait_p90_walkup_s": walkup.get("p90"),
                "walkups": walkup.get("n", 0),
                "throughput_per_h": peak_throughput(log),
                "bottleneck": params.bottleneck_station,
                "bottleneck_util": utilisation.get(params.bottleneck_station, 0.0),
                "busiest_station": max(utilisation, key=utilisation.get, default=None),
                "busiest_util": max(utilisation.values(), default=0.0),
                "crew_util": crew_utilisation(log, params, window=window),
            }
        )
    return result


def compare(names: list[str], seeds: list[int], *, base: str = BASE) -> list[ArmResult]:
    return [run_arm(ARMS[name], seeds, base=base) for name in names]


@dataclass
class SweepPoint:
    """One arm at one value of the swept parameter."""

    arm: str
    path: str
    value: float
    result: ArmResult

    def summary(self, key: str) -> tuple[float, float]:
        return self.result.summary(key)


def sweep(
    names: list[str],
    path: str,
    values: Sequence[float],
    seeds: list[int],
    *,
    base: str = BASE,
) -> list[SweepPoint]:
    """Run every arm at every value of one parameter.

    The point of a sweep here is not to find an optimum. Almost every input is
    still assumed, so an optimum would be an artefact of a guess. What a sweep
    answers is which assumptions the conclusion actually depends on: if an arm
    wins across the whole plausible range of a parameter, that parameter does
    not need measuring carefully, and if the arms cross somewhere inside the
    range, the crossing point is the thing to go and check.
    """
    points: list[SweepPoint] = []
    for name in names:
        for value in values:
            points.append(
                SweepPoint(
                    arm=name,
                    path=path,
                    value=value,
                    result=run_arm(
                        ARMS[name], seeds, base=base, overlay=overlay_for(path, value)
                    ),
                )
            )
    return points


def _units(key: str) -> tuple[float, str, int]:
    """How to show a metric: minutes, dollars, percent, or as it comes."""
    if key.endswith("_cents"):
        return (100.0, "$", 0)
    if key.endswith("_fraction"):
        return (0.01, "%", 0)
    if key.endswith("_s"):
        return (60.0, "min", 1)
    return (1.0, "", 1)


def render_sweep(points: list[SweepPoint], keys: Sequence[str]) -> str:
    """One row per value, one column per arm, for each metric asked for."""
    if not points:
        return "nothing swept"

    path = points[0].path
    arms = list(dict.fromkeys(point.arm for point in points))
    values = list(dict.fromkeys(point.value for point in points))
    lookup = {(point.arm, point.value): point for point in points}

    lines: list[str] = []
    for key in keys:
        scale, unit, places = _units(key)
        lines.append("")
        lines.append(f"{key}{f' ({unit})' if unit else ''} by {path}")
        header = f"{path.split('.')[-1]:>22}" + "".join(f"{arm:>20}" for arm in arms)
        lines.append(header)
        lines.append("-" * len(header))
        for value in values:
            row = f"{value:>22.4g}"
            for arm in arms:
                point = lookup.get((arm, value))
                if point is None:
                    row += f"{'-':>20}"
                    continue
                mean_value, half = point.summary(key)
                row += (
                    f"{mean_value / scale:>13.{places}f}"
                    f"±{half / scale:<6.{places}f}"
                )
            lines.append(row)
    return "\n".join(lines)


def render(results: list[ArmResult]) -> str:
    lines: list[str] = []
    for result in results:
        report = result.params.provenance_report()
        lines.append(f"{result.arm.name}: {result.arm.note}")
        lines.append(f"  {report.caption()}   bottleneck: {result.params.bottleneck_station}")

    header = (
        f"{'arm':<20}{'orders':>7}{'walkup p50':>12}{'walkup p90':>13}"
        f"{'served':>8}{'lost':>7}{'margin':>9}{'busiest station':>16}{'busy':>7}"
    )
    lines += ["", header, "-" * len(header)]

    for result in results:
        orders, _ = result.summary("orders")
        served, _ = result.summary("served")
        p50, p50_ci = result.summary("wait_p50_walkup_s")
        p90, p90_ci = result.summary("wait_p90_walkup_s")
        lost, _ = result.summary("lost_fraction")
        captured, _ = result.summary("captured_margin_cents")
        busy, _ = result.summary("busiest_util")
        station = result.rows[0]["busiest_station"] if result.rows else "-"
        lines.append(
            f"{result.arm.name:<20}{orders:>7.0f}"
            f"{p50 / 60:>9.1f}±{p50_ci / 60:<2.1f}"
            f"{p90 / 60:>10.1f}±{p90_ci / 60:<2.1f}"
            f"{served:>8.0f}{lost:>7.0%}{captured / 100:>9,.0f}{station:>16}{busy:>7.0%}"
        )

    lines.append("")
    lines.append(
        "walk-up waits in minutes over the busiest hour, since that is the queue "
        "people actually stand in;\nmargin in dollars kept across the day; mean of "
        "seeds with a 95% interval"
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run arms and compare them.")
    parser.add_argument("--arms", default=",".join(ARMS), help="comma separated arm names")
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--out", default="out")
    parser.add_argument(
        "--sweep", default=None,
        help="dotted parameter path, e.g. customers.preorder_adoption",
    )
    parser.add_argument(
        "--values", default=None,
        help="comma separated values for --sweep",
    )
    parser.add_argument(
        "--figures", action="store_true", help="write figures to --out",
    )
    args = parser.parse_args()

    names = [name.strip() for name in args.arms.split(",") if name.strip()]
    unknown = [name for name in names if name not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s) {unknown}; have {sorted(ARMS)}")

    seeds = list(range(args.seeds))

    if args.sweep:
        if not args.values:
            raise SystemExit("--sweep needs --values")
        values = [float(v) for v in args.values.split(",") if v.strip()]
        points = sweep(names, args.sweep, values, seeds, base=args.base)
        print(
            render_sweep(
                points,
                ["wait_p90_walkup_s", "lost_fraction", "captured_margin_cents"],
            )
        )
        print()
        print(points[0].result.params.provenance_report().detail())

        target = Path(args.out) / "sweep.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                [
                    {"arm": p.arm, "path": p.path, "value": p.value, "rows": p.result.rows}
                    for p in points
                ],
                indent=2,
            )
        )
        print(f"wrote {target}")

        if args.figures:
            from analysis.figures import sweep_figure

            print(f"wrote {sweep_figure(points, args.out)}")
        return

    results = compare(names, seeds, base=args.base)
    print(render(results))

    if args.figures:
        from analysis.figures import arm_figures

        for path in arm_figures(results, args.out):
            print(f"wrote {path}")

    target = Path(args.out) / "compare.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                result.arm.name: {
                    "note": result.arm.note,
                    "provenance": result.params.provenance_report().caption(),
                    "rows": result.rows,
                }
                for result in results
            },
            indent=2,
        )
    )
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
