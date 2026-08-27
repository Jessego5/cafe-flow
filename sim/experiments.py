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
        utilisation = station_utilisation(log, params, window=window)
        counts = state_counts(log)

        result.rows.append(
            {
                "seed": seed,
                "orders": counts.get("placed", 0),
                "served": counts.get("picked_up", 0),
                "in_flight": counts.get("in_flight", 0),
                "peak_from_s": window.start_s if window else None,
                "wait_p50_s": waits["p50"],
                "wait_p90_s": waits["p90"],
                "wait_p95_s": waits["p95"],
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

    header = f"{'arm':<12}{'orders':>8}{'wait p50':>10}{'wait p90':>12}{'wait p95':>12}" \
             f"{'peak /h':>10}{'busiest station':>20}{'busy':>8}{'crew':>8}"
    lines += ["", header, "-" * len(header)]

    for result in results:
        orders, _ = result.summary("orders")
        p50, p50_ci = result.summary("wait_p50_s")
        p90, p90_ci = result.summary("wait_p90_s")
        p95, p95_ci = result.summary("wait_p95_s")
        rate, _ = result.summary("throughput_per_h")
        busy, _ = result.summary("busiest_util")
        crew, _ = result.summary("crew_util")
        station = result.rows[0]["busiest_station"] if result.rows else "-"
        lines.append(
            f"{result.arm.name:<12}{orders:>8.0f}"
            f"{p50 / 60:>7.1f}±{p50_ci / 60:<2.1f}"
            f"{p90 / 60:>9.1f}±{p90_ci / 60:<2.1f}"
            f"{p95 / 60:>9.1f}±{p95_ci / 60:<2.1f}"
            f"{rate:>10.1f}{station:>20}{busy:>8.1%}{crew:>8.1%}"
        )

    lines.append("")
    lines.append("waits in minutes, over the busiest hour, mean of seeds with a 95% interval")
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
