"""M6 acceptance tests.

Done when: balks rise with volume; a very patient crowd never balks; and full
adoption of ordering ahead leaves no walk-ups at all.

Until this milestone the model assumed infinite patience, so a long queue cost
nothing. These tests are about the queue finally having a price.
"""

from __future__ import annotations

from statistics import mean

import numpy as np
import pytest

from analysis.metrics import balk_count_and_lost_margin
from core.params import load_params
from core.states import State
from core.types import Channel
from sim.arrivals import generate_arrivals
from sim.balking import (
    estimate_wait_s,
    nominal_seconds_per_order,
    observable_queue_depth,
    preorder_lead_s,
    sample_minutes,
)
from sim.engine import run

BASE = "params/base.yaml"
SEEDS = list(range(6))


@pytest.fixture(scope="module")
def params():
    return load_params(BASE)


def _with(tmp_path_factory, name: str, body: str):
    path = tmp_path_factory.mktemp("params") / f"{name}.yaml"
    path.write_text(body)
    return load_params(BASE, path)


def _census(params, seeds=SEEDS):
    return [run(params, seed).census() for seed in seeds]


# --------------------------------------------------------------------------
# the pieces
# --------------------------------------------------------------------------


def test_the_queue_a_customer_sees_excludes_the_shelf(params):
    """Drinks already on the handoff shelf are not part of the line."""
    assert observable_queue_depth(
        [State.PLACED, State.ACCEPTED, State.IN_PROGRESS, State.READY,
         State.PICKED_UP, State.BALKED]
    ) == 3
    assert observable_queue_depth([]) == 0


def test_the_estimate_is_people_ahead_times_how_long_each_takes(params):
    per_person = nominal_seconds_per_order(params, baristas=2)
    assert per_person > 0
    assert estimate_wait_s(0, per_person) == 0
    assert estimate_wait_s(5, per_person) == pytest.approx(5 * per_person)
    # more hands, shorter estimate
    assert nominal_seconds_per_order(params, 3) < per_person


def test_tolerances_are_drawn_from_the_configured_distribution(params):
    rng = np.random.default_rng(0)
    draws = [sample_minutes(params.customers.balk_tolerance_min, rng) for _ in range(4000)]
    assert np.median(draws) == pytest.approx(params.customers.balk_tolerance_min.median, rel=0.1)
    assert all(draw > 0 for draw in draws)


def test_ordering_ahead_means_one_class_block(params):
    lead = preorder_lead_s(params)
    ends = sorted(block.ends_at_s for block in params.arrivals.class_blocks)
    assert lead == pytest.approx(ends[1] - ends[0])


# --------------------------------------------------------------------------
# the acceptance criteria
# --------------------------------------------------------------------------


def test_balks_rise_with_volume(tmp_path_factory):
    """More people, more of them leaving. The whole point of the mechanism."""
    balks = []
    for capture_rate in (0.02, 0.055, 0.09, 0.13):
        params = _with(
            tmp_path_factory, f"capture_{capture_rate}",
            f"arrivals:\n  capture_rate: {capture_rate}\n  source: fitted\n",
        )
        balks.append(mean(census["balked"] for census in _census(params)))

    assert balks == sorted(balks), balks
    assert balks[0] < balks[-1]


def test_a_patient_crowd_never_balks(tmp_path_factory):
    params = _with(
        tmp_path_factory, "patient",
        "customers:\n"
        "  balk_tolerance_min: { dist: constant, value: 600.0 }\n"
        "  time_budget_min: { dist: constant, value: 600.0 }\n"
        "  source: assumed\n",
    )
    for census in _census(params):
        assert census["balked"] == 0
        assert census["abandoned"] == 0
        assert census["picked_up"] + census["in_flight_at_end"] == census["placed"]


def test_an_impatient_crowd_balks_at_almost_everything(tmp_path_factory):
    params = _with(
        tmp_path_factory, "impatient",
        "customers:\n"
        "  balk_tolerance_min: { dist: constant, value: 0.01 }\n"
        "  source: assumed\n",
    )
    census = _census(params, seeds=[0])[0]
    # only those who find the counter genuinely empty get served
    assert census["balked"] > census["picked_up"]


def test_full_adoption_leaves_no_walk_ups(tmp_path_factory):
    params = _with(
        tmp_path_factory, "all_preorder",
        "customers:\n  preorder_adoption: 1.0\n  source: assumed\n",
    )
    arrivals = generate_arrivals(params, np.random.default_rng(1))
    assert arrivals
    assert all(arrival.channel is Channel.PREORDER for arrival in arrivals)

    result = run(params, 1)
    channels = {
        event.channel for event in result.log if event.channel is not None
    }
    assert channels == {Channel.PREORDER}
    assert result.census()["balked"] == 0          # a committed customer never balks


def test_nobody_orders_ahead_when_adoption_is_zero(params):
    assert params.customers.preorder_adoption == 0.0
    arrivals = generate_arrivals(params, np.random.default_rng(1))
    assert all(arrival.channel is Channel.WALKUP for arrival in arrivals)
    assert all(arrival.wanted_at_s == arrival.at_s for arrival in arrivals)


# --------------------------------------------------------------------------
# what it does to the rest of the model
# --------------------------------------------------------------------------


def test_a_balked_order_is_never_made(params):
    result = run(params, 42)
    balked = {
        order_id for order_id, order in result.orders.items()
        if order.state is State.BALKED
    }
    assert balked

    worked_on = {
        event.order_id for event in result.log
        if event.station is not None and event.order_id in balked
    }
    assert worked_on == set()


def test_a_balk_records_what_the_customer_saw(params):
    result = run(params, 42)
    balks = [
        event for event in result.log
        if event.to_state == State.BALKED
    ]
    assert balks
    for event in balks:
        assert event.payload["estimated_wait_s"] > event.payload["tolerance_s"]
        assert event.payload["queue_depth"] >= 0
        assert event.payload["margin_cents"] > 0
        assert event.actor == "customer"


def test_conservation_still_holds_with_people_leaving(params):
    for seed in SEEDS:
        result = run(params, seed)
        assert result.conserved()
        census = result.census()
        assert census["balked"] > 0                # the assumed day is a busy one
        assert census["placed"] == len(result.arrivals)


def test_determinism_survives_the_extra_draws(params):
    first, second = run(params, 5), run(params, 5)
    assert first.log.digest() == second.log.digest()
    assert first.census() == second.census()


def test_the_lost_margin_is_readable_from_the_log_alone(params):
    result = run(params, 42)
    losses = balk_count_and_lost_margin(result.log)
    census = result.census()

    assert losses["balked"] == census["balked"]
    assert losses["abandoned"] == census["abandoned"]
    assert losses["placed"] == census["placed"]
    assert 0 < losses["lost_margin_cents"] < losses["offered_margin_cents"]
    assert losses["captured_margin_cents"] == (
        losses["offered_margin_cents"] - losses["lost_margin_cents"]
    )
    assert set(losses["by_reason"]) <= {"balked", "out_of_time", "no_show"}


def test_a_no_show_is_recorded_as_such(tmp_path_factory):
    params = _with(
        tmp_path_factory, "no_shows",
        "customers:\n"
        "  preorder_adoption: 1.0\n"
        "  no_show_rate: 0.5\n"
        "  balk_tolerance_min: { dist: constant, value: 600.0 }\n"
        "  time_budget_min: { dist: constant, value: 600.0 }\n"
        "  source: assumed\n",
    )
    result = run(params, 2)
    reasons = balk_count_and_lost_margin(result.log)["by_reason"]
    assert reasons.get("no_show", 0) > 0
    assert "balked" not in reasons


def test_a_preorder_is_collected_when_it_was_wanted(tmp_path_factory):
    params = _with(
        tmp_path_factory, "collect",
        "customers:\n"
        "  preorder_adoption: 1.0\n"
        "  no_show_rate: 0.0\n"
        "  time_budget_min: { dist: constant, value: 600.0 }\n"
        "  source: assumed\n",
    )
    result = run(params, 3)
    collected = 0
    for arrival in result.arrivals:
        order = result.orders.get(arrival.order_id)
        if order is None or order.state is not State.PICKED_UP:
            continue
        collected += 1
        assert order.entered_at(State.PICKED_UP) >= arrival.wanted_at_s - 1e-9
        assert order.placed_at_s <= arrival.wanted_at_s
    assert collected > 0
