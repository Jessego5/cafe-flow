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
from core.params import Params, load_params
from sim.engine import run

__all__ = ["Arm", "ARMS", "run_arm", "compare"]

BASE = "params/base.yaml"
CONFIDENCE_95 = 1.96


@dataclass(frozen=True, slots=True)
class Arm:
    """One configuration to run, as the overlays that produce it."""

    name: str
    overlays: tuple[str, ...] = ()
    note: str = ""

    def params(self, base: str = BASE) -> Params:
        return load_params(base, *self.overlays)


#: The two readings of the espresso bar. Both are assumed until the machine is
#: identified; running them side by side costs the question before answering it.
ARMS: dict[str, Arm] = {
    "manual_bar": Arm(
        "manual_bar",
        (),
        "pitcher and separate group head; milk drinks can be batched",
    ),
    "superauto": Arm(
        "superauto",
        ("params/experiments/superauto.yaml",),
        "one Schaerer super-automatic; no pitcher, so nothing batches",
    ),
    "adoption": Arm(
        "adoption",
        ("params/experiments/adoption.yaml",),
        "30% order ahead, so that share never sees the line",
    ),
    "adoption_batched": Arm(
        "adoption_batched",
        ("params/experiments/adoption.yaml", "params/experiments/batch.yaml"),
        "both: ordering ahead and running compatible work together",
    ),
    "batched": Arm(
        "batched",
        ("params/experiments/batch.yaml",),
        "manual bar, running compatible work together at every station that can",
    ),
    "superauto_batched": Arm(
        "superauto_batched",
        ("params/experiments/superauto.yaml", "params/experiments/batch.yaml"),
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


def run_arm(arm: Arm, seeds: list[int], *, base: str = BASE) -> ArmResult:
    """Run one arm across seeds, measuring the rush the log itself identifies."""
    params = arm.params(base)
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
    args = parser.parse_args()

    names = [name.strip() for name in args.arms.split(",") if name.strip()]
    unknown = [name for name in names if name not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s) {unknown}; have {sorted(ARMS)}")

    results = compare(names, list(range(args.seeds)), base=args.base)
    print(render(results))

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
