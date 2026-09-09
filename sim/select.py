"""Choose the scheduling policy for whatever cafe the config describes.

This is the piece that makes the project a tool rather than one cafe's answer.
Feed it a different `base.yaml` with a different menu, different stations and
different demand, and it works out which of the schedulers in `core.policies`
that operation should run, on the evidence of many simulated days.

Two rules keep it honest, and they matter more than the search:

**It will not switch on noise.** A challenger has to beat the incumbent by more
than the two confidence intervals put together. Two of the arms in this project
are statistically identical; a selector that picked between them by comparing
means would be inventing a difference and then acting on it.

**It will not select on an unvalidated model.** A model nobody has checked
against reality will still name a winner, confidently, and be wrong. Selection
against a mostly-assumed configuration requires saying so out loud.

The selection is written as a parameter overlay, so the app picks it up the way
it picks up everything else. The app never imports this module, or any part of
the simulator: it reads config.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import yaml

from core.params import load_params
from core.policies import POLICIES
from sim.experiments import Arm, ArmResult, run_arm

__all__ = [
    "Objective",
    "OBJECTIVES",
    "Candidate",
    "Selection",
    "decide",
    "select_policy",
]

#: How much of the configuration may still be guesswork before a selection is
#: refused. Above this the model is a sketch, and a sketch will still name a
#: winner.
MAX_ASSUMED = 0.60


@dataclass(frozen=True, slots=True)
class Objective:
    """What the operation is trying to do. There is no neutral answer."""

    key: str
    metric: str
    bigger_is_better: bool
    describe: str

    def better(self, challenger: float, incumbent: float) -> float:
        """How much better the challenger is, in the metric's own units."""
        return (
            challenger - incumbent if self.bigger_is_better else incumbent - challenger
        )


#: A cafe manager, a barista and a researcher want different things, and the
#: honest response is to make them say which.
OBJECTIVES: dict[str, Objective] = {
    "margin": Objective(
        "margin", "captured_margin_cents", True,
        "keep the most money: fewest customers lost, weighted by what they were worth",
    ),
    "wait": Objective(
        "wait", "wait_p90_walkup_s", False,
        "shortest tail for the person standing in the queue",
    ),
    "lost": Objective(
        "lost", "lost_fraction", False,
        "fewest people who leave or give up, whatever they were buying",
    ),
    "throughput": Objective(
        "throughput", "throughput_per_h", True,
        "most drinks out of the bar in the busiest hour",
    ),
}


@dataclass
class Candidate:
    """One policy's showing, with the interval that decides whether to believe it."""

    policy: str
    arm: str
    value: float
    half_width: float
    rows: list[dict] = field(default_factory=list)

    @property
    def low(self) -> float:
        return self.value - self.half_width

    @property
    def high(self) -> float:
        return self.value + self.half_width


@dataclass
class Selection:
    """What was chosen, what it beat, and whether the difference is real."""

    objective: Objective
    incumbent: str
    chosen: str
    candidates: list[Candidate]
    confident: bool
    provenance: str
    assumed_fraction: float
    seeds: int

    @property
    def changed(self) -> bool:
        return self.chosen != self.incumbent

    @property
    def trustworthy(self) -> bool:
        return self.assumed_fraction <= MAX_ASSUMED

    def best(self) -> Candidate:
        return next(c for c in self.candidates if c.policy == self.chosen)

    def overlay(self) -> dict:
        return {
            "policy": {"name": self.chosen, "source": "fitted"},
            "meta": {"scenario": f"selected_{self.chosen}", "source": "assumed"},
        }

    def render(self) -> str:
        scale, unit = _units(self.objective.metric)
        lines = [
            f"objective: {self.objective.key}, {self.objective.describe}",
            f"{self.seeds} seeded days per policy, {self.provenance}",
            "",
            f"{'policy':<18}{'':>4}{self.objective.metric:>26}",
            "-" * 48,
        ]
        ordered = sorted(
            self.candidates,
            key=lambda c: c.value,
            reverse=self.objective.bigger_is_better,
        )
        for candidate in ordered:
            mark = "*" if candidate.policy == self.chosen else " "
            lines.append(
                f"{candidate.policy:<18}{mark:>4}"
                f"{candidate.value / scale:>18.1f}±{candidate.half_width / scale:<6.1f}{unit}"
            )

        lines.append("")
        if not self.changed:
            lines.append(f"keeping {self.incumbent}: nothing beat it by more than the noise")
        elif self.confident:
            lines.append(
                f"switch {self.incumbent} -> {self.chosen}: "
                f"clear of the incumbent's interval"
            )
        else:
            lines.append(
                f"keeping {self.incumbent}: {self.chosen} looks better on the mean but "
                f"the intervals overlap, which is not a difference"
            )

        if not self.trustworthy:
            lines += [
                "",
                f"NOT SAFE TO APPLY: {self.assumed_fraction:.0%} of this configuration is "
                "still assumed.",
                "A model nobody has checked against the floor will still name a winner,",
                "confidently, and be wrong. Calibrate first, or pass --force and own it.",
            ]
        return "\n".join(lines)


def _units(metric: str) -> tuple[float, str]:
    if metric.endswith("_cents"):
        return (100.0, " $")
    if metric.endswith("_fraction"):
        return (0.01, " %")
    if metric.endswith("_s"):
        return (60.0, " min")
    return (1.0, "")


def decide(
    candidates: Sequence[Candidate], incumbent: str, goal: Objective
) -> tuple[str, bool]:
    """Which policy to run, and whether the evidence actually says so.

    Two rules, and both are about not fooling yourself:

    A challenger must clear the incumbent's confidence interval, not merely beat
    its mean. Two policies whose intervals overlap have not been told apart, and
    switching between them is acting on noise.

    A tie goes to the simpler policy. `POLICIES` is ordered simplest first, and
    adopting a scheduler that is harder to explain to a barista, for a
    difference the evidence cannot see, is a bad trade at any confidence.
    """
    held = next(c for c in candidates if c.policy == incumbent)
    leader = (max if goal.bigger_is_better else min)(candidates, key=lambda c: c.value)

    order = list(POLICIES)
    tied = [
        candidate for candidate in candidates
        if not (candidate.low > leader.high or candidate.high < leader.low)
    ]
    leader = min(
        tied, key=lambda candidate: order.index(candidate.policy)
        if candidate.policy in order else len(order)
    )

    separated = (
        (leader.low > held.high) if goal.bigger_is_better else (leader.high < held.low)
    )
    confident = leader.policy != incumbent and separated
    return (leader.policy if confident else incumbent), confident


def select_policy(
    params_files: Sequence[str],
    *,
    objective: str = "margin",
    seeds: int = 20,
    policies: Sequence[str] | None = None,
) -> Selection:
    """Run every policy on this configuration and say which one it should use."""
    if objective not in OBJECTIVES:
        raise SystemExit(f"unknown objective {objective!r}; have {sorted(OBJECTIVES)}")

    goal = OBJECTIVES[objective]
    base = load_params(*params_files)
    incumbent = base.policy.name
    wanted = list(policies or POLICIES)
    seed_list = list(range(seeds))

    candidates: list[Candidate] = []
    for policy in wanted:
        # Every file after the first is an overlay and has to reach the runs.
        # Reading the incumbent from the whole stack while running the arms on
        # the base alone silently compares policies against a configuration
        # nobody asked about.
        arm = Arm(policy, tuple(str(path) for path in params_files[1:]), f"policy {policy}")
        result: ArmResult = run_arm(
            arm,
            seed_list,
            base=str(params_files[0]),
            overlay={"policy": {"name": policy}},
        )
        value, half = result.summary(goal.metric)
        candidates.append(
            Candidate(policy=policy, arm=arm.name, value=value, half_width=half,
                      rows=result.rows)
        )

    chosen, confident = decide(candidates, incumbent, goal)
    report = base.provenance_report()
    return Selection(
        objective=goal,
        incumbent=incumbent,
        chosen=chosen,
        candidates=candidates,
        confident=confident,
        provenance=report.detail(),
        assumed_fraction=report.assumed_fraction,
        seeds=seeds,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Choose the scheduler for whatever cafe the config describes."
    )
    parser.add_argument("--params", nargs="+", default=["params/base.yaml"])
    parser.add_argument(
        "--objective", default="margin",
        help=" | ".join(f"{key}: {goal.describe}" for key, goal in OBJECTIVES.items()),
    )
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--apply", action="store_true", help="write the overlay")
    parser.add_argument("--out", default="params/selected.yaml")
    parser.add_argument(
        "--force", action="store_true",
        help="apply even though the configuration is mostly assumed",
    )
    args = parser.parse_args()

    selection = select_policy(
        args.params, objective=args.objective, seeds=args.seeds
    )
    print(selection.render())

    evidence = Path("out") / "selection.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        json.dumps(
            {
                "objective": selection.objective.key,
                "incumbent": selection.incumbent,
                "chosen": selection.chosen,
                "confident": selection.confident,
                "provenance": selection.provenance,
                "candidates": [
                    {
                        "policy": c.policy,
                        "value": c.value,
                        "half_width": c.half_width,
                        "rows": c.rows,
                    }
                    for c in selection.candidates
                ],
            },
            indent=2,
        )
    )
    print(f"wrote {evidence}")

    if not args.apply:
        return
    if not selection.trustworthy and not args.force:
        raise SystemExit(1)

    target = Path(args.out)
    target.write_text(
        "# Written by sim/select.py. The app reads this like any other overlay;\n"
        "# it never imports the simulator.\n"
        f"# objective: {selection.objective.key} "
        f"({selection.objective.describe})\n"
        f"# {selection.seeds} seeded days per policy; {selection.provenance}\n\n"
        + yaml.safe_dump(selection.overlay(), sort_keys=False)
    )
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
