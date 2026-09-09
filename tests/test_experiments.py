"""
These are the tests for the two readings of the espresso bar and the machinery
that compares them. The machine photographed at the cafe carries a Schaerer
logo, and if it is a super-automatic then there is no pitcher, nothing batches,
and the separate wand and group head are one serialised resource. Both readings
stay assumed until the model number is confirmed, so both have to run. Run them
with pytest.
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
    """
    The plan calls milk batching the core throughput mechanism. On a
    super-automatic there is no pitcher to fill once and pour four times."""
    model = StationCapacityModel(superauto)
    assert superauto.bottleneck.batch_key is None

    four = make_order("o", superauto, lines=[Line("latte", "oat", "hot")] * 4).items
    assert model.batch_cost(four) == sum(model.cost(item) for item in four)
    assert [len(group) for group in model.group(four)] == [1, 1, 1, 1]


def test_an_iced_latte_still_needs_the_machine(manual, superauto):
    """
    The correction the photograph forces: iced drinks skip the frothing, not
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

    def dimensions(drink, variant=None):
        task = superauto.menu_item(drink).plan(variant)[0][0]
        return task.shots or 0, task.oz or 0

    for drink, variant in (("espresso", None), ("americano", None),
                           ("latte", "hot"), ("matcha_latte", None)):
        shots, oz = dimensions(drink, variant)
        milk = "oat" if superauto.menu_item(drink).requires_milk else None
        assert cost(drink, milk, variant) == pytest.approx(
            machine.shot_s * shots + machine.per_6oz_s * oz / 6
        )

    assert dimensions("matcha_latte")[0] == 0        # froths, no shot
    assert cost("drip_coffee") == 0.0                # never touches it


def test_both_arms_run_and_conserve():
    seeds = [0, 1]
    for name in ("manual_bar", "superauto"):
        result = run_arm(ARMS[name], seeds)
        assert len(result.rows) == len(seeds)
        for row in result.rows:
            assert row["orders"] > 0
            # people leave now, so the identity has more terms than it did
            assert (
                row["served"] + row["in_flight"] + row["balked"] + row["abandoned"]
                == row["orders"]
            )
            assert row["wait_p90_s"] > 0
            assert 0 < row["captured_margin_cents"] < row["orders"] * 100_00


def test_the_arms_face_identical_demand():
    """
    Same seed, same arrivals: the two arms differ only in the bar, so any
    difference between them is the bar."""
    manual = run_arm(ARMS["manual_bar"], [3])
    superauto = run_arm(ARMS["superauto"], [3])
    assert manual.rows[0]["orders"] == superauto.rows[0]["orders"]


def test_the_espresso_bar_is_not_the_constraint_either_way(manual, superauto):
    """
    Recorded because it is the answer to the question the overlay was built
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


# --------------------------------------------------------------------------
# M7: sweeps
# --------------------------------------------------------------------------


def test_a_swept_value_goes_through_the_normal_merge():
    from core.params import load_params, overlay_for

    swept = load_params("params/base.yaml", overlay=overlay_for("customers.preorder_adoption", 0.4))
    assert swept.customers.preorder_adoption == 0.4


def test_a_swept_value_is_marked_as_an_assumption():
    """
    A sweep must not launder a guess into a citation: the published press
    time stops being published the moment something else is put in its place."""
    from core.params import load_params, overlay_for

    base = load_params("params/base.yaml")
    assert base.source_of("stations.panini_press.run_s") == "published"

    swept = load_params("params/base.yaml", overlay=overlay_for("stations.panini_press.run_s", 150))
    assert swept.station("panini_press").run_s == 150
    assert swept.source_of("stations.panini_press.run_s") == "assumed"
    # everything it did not touch keeps its own provenance
    assert swept.source_of("stations.group_head.shot_s") == "published"


def test_sweeping_a_parameter_that_does_not_exist_fails_by_name():
    from core.params import ConfigError, load_params, overlay_for

    with pytest.raises(ConfigError, match="press_run_s"):
        load_params("params/base.yaml", overlay=overlay_for("stations.panini_press.press_run_s", 1))


def test_a_sweep_runs_every_arm_at_every_value():
    from sim.experiments import sweep

    points = sweep(["manual_bar", "batched"], "customers.preorder_adoption", [0.0, 0.5], [0, 1])
    assert len(points) == 4
    assert {(p.arm, p.value) for p in points} == {
        ("manual_bar", 0.0), ("manual_bar", 0.5), ("batched", 0.0), ("batched", 0.5)
    }
    for point in points:
        assert len(point.result.rows) == 2
        assert point.result.params.customers.preorder_adoption == point.value


def test_more_people_ordering_ahead_means_a_shorter_line(tmp_path):
    """
    The mechanism the plan cares about, swept rather than asserted at a
    single point.

    This asserted `lost_fraction` falling until the arms were moved onto the
    observed configuration. It passed for a reason that turned out not to be
    true: patience was being spent *after* the register, so the model shed
    customers who had already ordered, and ordering ahead 'rescued' them.
    Once the budget is spent in the line where it belongs, almost nobody is
    lost at all, which is what watching the cafe found, and there is no
    lost revenue left for the app to recover.

    What survives is the claim worth making anyway: ordering ahead takes people
    out of the queue, so the queue gets shorter for everyone still in it. The
    tail is where it shows, which is why this reads p90 and not the median.
    """
    from sim.experiments import sweep

    points = sweep(["batched"], "customers.preorder_adoption", [0.0, 0.3, 0.6], list(range(6)))
    p90 = [point.summary("wait_p90_walkup_s")[0] for point in points]
    assert p90 == sorted(p90, reverse=True), p90
    assert p90[0] - p90[-1] > 60.0, p90


def test_figures_carry_their_provenance(tmp_path):
    from analysis.figures import arm_figures, sweep_figure, utilisation_figure
    from sim.experiments import compare, sweep

    results = compare(["manual_bar", "batched"], [0, 1])
    written = arm_figures(results, tmp_path)
    assert len(written) == 2
    for path in written:
        assert path.exists() and path.stat().st_size > 5_000

    points = sweep(["batched"], "customers.preorder_adoption", [0.0, 0.4], [0, 1])
    figure = sweep_figure(points, tmp_path)
    assert figure.exists() and figure.stat().st_size > 5_000

    load = utilisation_figure(
        {"panini_press": 0.72, "steam_wand": 0.11, None: 0.4},
        "provenance: 79% assumed", tmp_path,
    )
    assert load.exists() and load.stat().st_size > 5_000
