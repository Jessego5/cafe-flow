"""Choosing a scheduler for whatever operation the config describes.

This is the piece that makes the project a tool rather than one cafe's answer,
so most of these tests are about the two ways it refuses: it will not switch on
noise, and it will not select against a model nobody has checked.
"""

from __future__ import annotations

import pytest
import yaml

from core.params import load_params
from core.policies import POLICIES
from sim.select import MAX_ASSUMED, OBJECTIVES, Candidate, decide, select_policy

BASE = "params/base.yaml"
OTHER = "params/examples/espresso_bar.yaml"


def candidate(policy: str, value: float, half: float) -> Candidate:
    return Candidate(policy=policy, arm=policy, value=value, half_width=half)


# --------------------------------------------------------------------------
# the decision rule
# --------------------------------------------------------------------------


def test_a_clear_winner_is_adopted():
    goal = OBJECTIVES["margin"]
    chosen, confident = decide(
        [candidate("fifo", 500, 10), candidate("batch_milk", 560, 10)], "fifo", goal
    )
    assert (chosen, confident) == ("batch_milk", True)


def test_overlapping_intervals_are_not_a_difference():
    """The whole point. Two policies whose intervals overlap have not been told
    apart, and switching between them is acting on noise."""
    goal = OBJECTIVES["margin"]
    chosen, confident = decide(
        [candidate("fifo", 508, 19), candidate("batch_milk", 531, 19)], "fifo", goal
    )
    assert (chosen, confident) == ("fifo", False)


def test_the_same_gap_becomes_a_decision_once_the_intervals_shrink():
    """More seeded days, narrower interval, same means: now it switches. A
    selector that never selects would be no use."""
    goal = OBJECTIVES["margin"]
    chosen, confident = decide(
        [candidate("fifo", 508, 6), candidate("batch_milk", 531, 6)], "fifo", goal
    )
    assert (chosen, confident) == ("batch_milk", True)


def test_smaller_is_better_for_a_wait():
    goal = OBJECTIVES["wait"]
    chosen, _ = decide(
        [candidate("fifo", 700, 30), candidate("batch_milk", 500, 30)], "fifo", goal
    )
    assert chosen == "batch_milk"
    # and it does not switch to something worse
    chosen, _ = decide(
        [candidate("fifo", 500, 30), candidate("batch_milk", 700, 30)], "fifo", goal
    )
    assert chosen == "fifo"


def test_a_tie_goes_to_the_simpler_policy():
    """Adopting a scheduler that is harder to explain to a barista, for a
    difference the evidence cannot see, is a bad trade at any confidence."""
    goal = OBJECTIVES["margin"]
    tied = [
        candidate("fifo", 500, 5),
        candidate("batch_milk", 560, 5),
        candidate("bounded_reorder", 560, 5),
    ]
    chosen, confident = decide(tied, "fifo", goal)
    assert chosen == "batch_milk"
    assert list(POLICIES).index("batch_milk") < list(POLICIES).index("bounded_reorder")
    assert confident


def test_an_incumbent_that_is_already_best_is_kept():
    goal = OBJECTIVES["margin"]
    chosen, confident = decide(
        [candidate("fifo", 600, 5), candidate("batch_milk", 500, 5)], "fifo", goal
    )
    assert (chosen, confident) == ("fifo", False)


# --------------------------------------------------------------------------
# selecting against a real configuration
# --------------------------------------------------------------------------


def test_it_runs_every_policy_and_names_one():
    selection = select_policy([BASE], objective="margin", seeds=3)
    assert {c.policy for c in selection.candidates} == set(POLICIES)
    assert selection.chosen in POLICIES
    assert selection.incumbent == load_params(BASE).policy.name
    assert selection.seeds == 3


def test_it_will_not_select_against_a_model_nobody_has_checked():
    """A model that is mostly assumed will still name a winner, confidently,
    and be wrong."""
    selection = select_policy([BASE], objective="margin", seeds=2)
    assert selection.assumed_fraction > MAX_ASSUMED
    assert selection.trustworthy is False
    assert "NOT SAFE TO APPLY" in selection.render()


def test_the_selection_is_just_a_parameter_overlay(tmp_path):
    """The app never imports the simulator. It reads config, like always."""
    selection = select_policy([BASE], objective="wait", seeds=2)
    path = tmp_path / "selected.yaml"
    path.write_text(yaml.safe_dump(selection.overlay(), sort_keys=False))

    merged = load_params(BASE, path)
    assert merged.policy.name == selection.chosen
    assert merged.source_of("policy.name") == "fitted"

    from core.policies import make_policy

    assert make_policy(merged).name == selection.chosen


def test_a_different_operation_gets_its_own_answer():
    """The claim the whole design rests on: feed it another cafe and the
    machinery works out what that one should do, not what this one does."""
    other = load_params(OTHER)
    assert other.cafe.name != load_params(BASE).cafe.name
    assert set(other.menu) != set(load_params(BASE).menu)

    selection = select_policy([OTHER], objective="margin", seeds=3)
    assert selection.chosen in POLICIES
    assert {c.policy for c in selection.candidates} == set(POLICIES)
    assert selection.objective.key == "margin"


def test_every_objective_is_a_metric_the_runs_actually_produce():
    selection = select_policy([BASE], objective="margin", seeds=2)
    rows = selection.candidates[0].rows
    for goal in OBJECTIVES.values():
        assert goal.metric in rows[0], goal.key


def test_an_unknown_objective_is_refused():
    with pytest.raises(SystemExit, match="unknown objective"):
        select_policy([BASE], objective="vibes", seeds=1)


# --------------------------------------------------------------------------
# every configuration that ships
# --------------------------------------------------------------------------

EXAMPLES = [
    "params/base.yaml",
    "params/examples/espresso_bar.yaml",
    "params/examples/maven_roasters.yaml",
]


@pytest.mark.parametrize("config", EXAMPLES)
def test_every_example_configuration_runs_and_can_be_selected_for(config):
    """Three operations built three different ways — a menu board, a hand
    written sketch, and a transaction log — and the same machinery answers for
    all of them."""
    params = load_params(config)
    selection = select_policy([config], objective="margin", seeds=2)
    assert selection.chosen in POLICIES
    assert selection.incumbent == params.policy.name
    assert len(selection.candidates) == len(POLICIES)


def test_the_configurations_really_are_different_operations():
    loaded = [load_params(config) for config in EXAMPLES]
    assert len({params.cafe.name for params in loaded}) == len(EXAMPLES)
    assert len({tuple(sorted(params.menu)) for params in loaded}) == len(EXAMPLES)
    assert len({tuple(sorted(params.stations)) for params in loaded}) == len(EXAMPLES)

    models = {params.arrivals.model for params in loaded}
    assert models == {"class_blocks", "profile"}


def test_a_synthetic_configuration_says_it_is_synthetic():
    """A generated dataset is realistic in shape and is not a record of
    anything that happened. Marking it observed would be the one lie the
    provenance system exists to prevent."""
    maven = load_params("params/examples/maven_roasters.yaml")
    counts = maven.provenance_report().counts
    assert counts.get("synthetic", 0) > 0
    assert counts.get("observed", 0) == 0
    assert maven.source_of("arrivals.profile.rate_per_hour.0") == "synthetic"
