"""The two readings of the espresso bar, and the machinery that compares them.

The machine photographed at the cafe carries a Schaerer logo. If it is a
super-automatic then there is no pitcher, nothing batches, and the separate
wand and group head are one serialised resource. Both readings are assumed
until the model number is confirmed, so both have to run.
"""

from __future__ import annotations

import pytest

from analysis.metrics import station_utilisation, busiest_window
from core.capacity import StationCapacityModel
from core.menu import make_item, make_order
from core.params import load_params
from core.types import Line
from sim.experiments import ARMS, run_arm

SUPERAUTO = "params/experiments/superauto.yaml"


@pytest.fixture(scope="module")
def manual():
    return load_params("params/base.yaml")


@pytest.fixture(scope="module")
def superauto():
    return load_params("params/base.yaml", SUPERAUTO)


def test_the_overlay_replaces_the_espresso_bar(superauto):
    assert "steam_wand" not in superauto.stations
    assert "group_head" not in superauto.stations
    assert "espresso_machine" in superauto.stations
    assert superauto.bottleneck_station == "espresso_machine"
    # the brew taps and the food side are untouched
    assert {"brew_tap", "cold_bar", "panini_press", "food_counter", "register"} <= set(
        superauto.stations
    )


def test_nothing_batches_without_a_pitcher(superauto):
    """The plan calls milk batching the core throughput mechanism. On a
    super-automatic there is no pitcher to fill once and pour four times."""
    model = StationCapacityModel(superauto)
    assert superauto.bottleneck.batch_key is None

    four = make_order("o", superauto, lines=[Line("latte", "oat", "hot")] * 4).items
    assert model.batch_cost(four) == sum(model.cost(item) for item in four)
    assert [len(group) for group in model.group(four)] == [1, 1, 1, 1]


def test_an_iced_latte_still_needs_the_machine(manual, superauto):
    """The correction the photograph forces: iced drinks skip the frothing, not
    the shot. Under the manual reading they cost the bottleneck nothing."""
    def cost(params, variant):
        model = StationCapacityModel(params)
        item = make_item("latte", params, order_id="o", item_id="i",
                         milk_type="oat", variant=variant)
        return model.cost(item)

    assert cost(manual, "iced") == 0.0
    assert cost(superauto, "iced") > 0.0
    assert cost(superauto, "iced") < cost(superauto, "hot")


def test_the_machine_prices_shots_and_milk_separately(superauto):
    machine = superauto.station("espresso_machine")
    model = StationCapacityModel(superauto)

    def cost(drink, milk=None, variant=None):
        item = make_item(drink, superauto, order_id="o", item_id="i",
                         milk_type=milk, variant=variant)
        return model.cost(item)

    assert cost("espresso") == machine.shot_s
    assert cost("americano") == 2 * machine.shot_s
    assert cost("latte", "oat", "hot") == machine.shot_s + machine.per_6oz_s * (8 / 6)
    assert cost("matcha_latte", "oat") == machine.per_6oz_s * (8 / 6)   # froths, no shot
    assert cost("drip_coffee") == 0.0                                   # never touches it


def test_both_arms_run_and_conserve():
    seeds = [0, 1]
    for name in ("manual_bar", "superauto"):
        result = run_arm(ARMS[name], seeds)
        assert len(result.rows) == len(seeds)
        for row in result.rows:
            assert row["orders"] > 0
            assert row["served"] + row["in_flight"] == row["orders"]
            assert row["wait_p90_s"] > 0


def test_the_arms_face_identical_demand():
    """Same seed, same arrivals: the two arms differ only in the bar, so any
    difference between them is the bar."""
    manual = run_arm(ARMS["manual_bar"], [3])
    superauto = run_arm(ARMS["superauto"], [3])
    assert manual.rows[0]["orders"] == superauto.rows[0]["orders"]


def test_the_espresso_bar_is_not_the_constraint_either_way(manual, superauto):
    """Recorded because it is the answer to the question the overlay was built
    to ask, and because it should fail loudly if demand or service times ever
    move enough to change it."""
    from sim.engine import run

    for params in (manual, superauto):
        day = run(params, 5)
        window = busiest_window(day.log)
        utilisation = station_utilisation(day.log, params, window=window)
        assert utilisation[params.bottleneck_station] < 0.25
        assert max(utilisation, key=utilisation.get) == "panini_press"


def test_the_summary_carries_a_confidence_interval():
    result = run_arm(ARMS["manual_bar"], [0, 1, 2])
    value, half_width = result.summary("wait_p90_s")
    assert value > 0
    assert half_width > 0
    assert result.summary("nonexistent") != result.summary("wait_p90_s")
