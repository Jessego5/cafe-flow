"""M5: what runs together, and what that buys.

`batch_size: 3` on the press was inert until now — it priced a batch in
`core.capacity` but nothing in the engine ever formed one. These tests pin down
both halves: the policy picks the work, and the engine actually runs it in one
cycle.
"""

from __future__ import annotations

from collections import Counter

import pytest
import simpy

from core.capacity import StationCapacityModel
from core.events import EventType
from core.menu import make_item, make_order
from core.params import ConfigError, load_params
from core.states import State
from core.types import Channel, Line
from sim.arrivals import Arrival
from sim.engine import run
from sim.policies import BatchPolicy, FIFOPolicy, Pending, make_policy

BASE = "params/base.yaml"
BATCH = "params/experiments/batch.yaml"


@pytest.fixture(scope="module")
def params():
    return load_params(BASE)


@pytest.fixture(scope="module")
def batching():
    return load_params(BASE, BATCH)


def _pending(params, station, drink, milk=None, variant=None, at=0.0, index=0):
    item = make_item(drink, params, order_id=f"o{index}", item_id=f"o{index}-0",
                     milk_type=milk, variant=variant)
    order = make_order(f"o{index}", params, lines=[Line(drink, milk, variant)])
    return Pending(
        task=item.tasks[0], order=order, item=item, station=station,
        submitted_at=at, done=simpy.Environment().event(),
    )


# --------------------------------------------------------------------------
# the policies
# --------------------------------------------------------------------------


def test_fifo_takes_one_at_a_time(params):
    policy = FIFOPolicy(params)
    queue = [
        _pending(params, "panini_press", "bacon_egg_cheese_bagel", index=index)
        for index in range(4)
    ]
    assert policy.next_batch("panini_press", queue, 0.0) == [queue[0]]


def test_batching_fills_the_press_to_its_capacity(params):
    policy = BatchPolicy(params)
    queue = [
        _pending(params, "panini_press", "bacon_egg_cheese_bagel", index=index)
        for index in range(5)
    ]
    batch = policy.next_batch("panini_press", queue, 0.0)
    assert len(batch) == params.station("panini_press").batch_size == 3
    assert batch == queue[:3]


def test_batching_fills_the_pitcher_and_stops(params):
    policy = BatchPolicy(params)
    queue = [
        _pending(params, "steam_wand", "latte", "oat", "hot", index=index)
        for index in range(6)
    ]
    batch = policy.next_batch("steam_wand", queue, 0.0)
    assert len(batch) == 4                       # 8oz each against a 32oz pitcher
    assert sum(entry.item.tasks[0].oz for entry in batch) == 32


def test_different_milks_are_not_grouped(params):
    policy = BatchPolicy(params)
    queue = [
        _pending(params, "steam_wand", "latte", "oat", "hot", index=0),
        _pending(params, "steam_wand", "latte", "whole", "hot", index=1),
        _pending(params, "steam_wand", "latte", "oat", "hot", index=2),
    ]
    batch = policy.next_batch("steam_wand", queue, 0.0)
    assert [entry.item.milk_type for entry in batch] == ["oat", "oat"]
    assert queue[1] not in batch


def test_work_outside_the_lookahead_waits(params):
    policy = BatchPolicy(params, lookahead_s=60.0)
    queue = [
        _pending(params, "panini_press", "bacon_egg_cheese_bagel", at=0.0, index=0),
        _pending(params, "panini_press", "bacon_egg_cheese_bagel", at=30.0, index=1),
        _pending(params, "panini_press", "bacon_egg_cheese_bagel", at=600.0, index=2),
    ]
    batch = policy.next_batch("panini_press", queue, 600.0)
    assert len(batch) == 2


def test_a_policy_that_is_not_built_fails_at_load(tmp_path):
    overlay = tmp_path / "reorder.yaml"
    overlay.write_text("policy:\n  name: bounded_reorder\n")
    params = load_params(BASE, overlay)
    with pytest.raises(ConfigError, match="not implemented"):
        make_policy(params)


def test_the_overlay_selects_the_batching_policy(params, batching):
    assert make_policy(params).name == "fifo"
    assert make_policy(batching).name == "batch_milk"


# --------------------------------------------------------------------------
# the engine actually runs them together
# --------------------------------------------------------------------------


def _three_sandwiches(params, at_s=None):
    # 11:00: three baristas on, so all three registers clear together and all
    # three sandwiches reach the press queue at the same moment
    at_s = 11 * 3600 if at_s is None else at_s
    return [
        Arrival(f"c{index}", f"o{index}", float(at_s), Channel.WALKUP,
                (Line("bacon_egg_cheese_bagel", None, None),), "test")
        for index in range(3)
    ]


def test_three_sandwiches_take_three_cycles_under_fifo(params):
    press = params.station("panini_press")
    result = run(params, 0, arrivals=_three_sandwiches(params))

    spans = [
        event.t_s for event in result.log
        if event.station == "panini_press" and event.type is EventType.STATION_END
    ]
    assert len(spans) == 3
    assert max(spans) - min(spans) == pytest.approx(2 * press.run_s)


def test_three_sandwiches_take_one_cycle_when_batched(batching):
    """The fix: `batch_size: 3` now means something in the engine, not only in
    the cost model."""
    press = batching.station("panini_press")
    result = run(batching, 0, arrivals=_three_sandwiches(batching))

    ends = [
        event for event in result.log
        if event.station == "panini_press" and event.type is EventType.STATION_END
    ]
    assert len(ends) == 1
    assert ends[0].payload["size"] == 3
    assert ends[0].payload["duration_s"] == press.run_s

    formed = [event for event in result.log if event.type is EventType.BATCH_FORMED]
    assert len(formed) == 1
    assert sorted(formed[0].payload["orders"]) == ["o0", "o1", "o2"]
    assert formed[0].payload["attended"] is False

    ready = [result.orders[f"o{index}"].entered_at(State.READY) for index in range(3)]
    assert max(ready) - min(ready) < press.run_s      # they land together


def test_batching_never_costs_more_than_one_at_a_time(params, batching):
    """Across seeds, on the same demand."""
    for seed in (0, 1, 2):
        one_by_one = run(params, seed)
        together = run(batching, seed)
        assert len(one_by_one.arrivals) == len(together.arrivals)

        def press_seconds(result):
            return sum(
                event.payload["duration_s"]
                for event in result.log
                if event.station == "panini_press" and event.type is EventType.STATION_START
            )

        assert press_seconds(together) < press_seconds(one_by_one)


def test_batching_keeps_the_invariants(batching):
    for seed in (0, 3):
        first, second = run(batching, seed), run(batching, seed)
        assert first.log.digest() == second.log.digest()
        assert first.conserved()

        census = first.census()
        assert census["placed"] == len(first.arrivals)


def test_a_batch_never_exceeds_what_the_station_allows(batching):
    result = run(batching, 7)
    press_limit = batching.station("panini_press").batch_size
    wand_limit = batching.station("steam_wand").max_batch_oz

    for event in result.log:
        if event.type is not EventType.BATCH_FORMED:
            continue
        if event.station == "panini_press":
            assert event.payload["size"] <= press_limit
        if event.station == "steam_wand":
            model = StationCapacityModel(batching, "steam_wand")
            assert event.payload["duration_s"] <= (
                batching.station("steam_wand").setup_s
                + batching.station("steam_wand").per_6oz_s * wand_limit / 6
            ) + 1e-9


def test_batches_only_ever_hold_compatible_work(batching):
    """Every formed batch is one milk type, because the wand is what makes
    milk type matter."""
    result = run(batching, 11)
    items = {item.item_id: item for order in result.orders.values() for item in order.items}

    for event in result.log:
        if event.type is not EventType.BATCH_FORMED or event.station != "steam_wand":
            continue
        milks = {items[item_id].milk_type for item_id in event.payload["items"]}
        assert len(milks) == 1, milks


def test_where_the_saving_actually_comes_from(params, batching):
    """On the observed menu it is the press, not the milk. Recorded so it fails
    loudly if the mix or the service times ever move enough to change it."""
    counts: Counter = Counter()
    for seed in range(4):
        for event in run(batching, seed).log:
            if event.type is EventType.BATCH_FORMED:
                counts[event.station] += event.payload["size"]

    assert counts["panini_press"] > counts.get("steam_wand", 0)


def test_the_press_does_not_hold_itself_idle_waiting_for_a_fuller_batch(batching):
    """It groups what is already waiting and starts. Holding a station idle in
    the hope that a third sandwich turns up is a different policy, and a
    riskier one: it trades a certain delay for a possible saving."""
    at_s = params_start = batching.meta.start_s + 3600      # 08:00, two baristas
    arrivals = [
        Arrival(f"c{index}", f"o{index}", float(at_s), Channel.WALKUP,
                (Line("bacon_egg_cheese_bagel", None, None),), "test")
        for index in range(3)
    ]
    result = run(batching, 0, arrivals=arrivals)

    sizes = [
        event.payload["size"] for event in result.log
        if event.station == "panini_press" and event.type is EventType.STATION_END
    ]
    # two registers clear together and go straight in; the third follows alone
    assert sizes == [2, 1]


def test_items_on_one_order_are_worked_one_after_another(batching):
    """A known limit of the dispatcher, recorded rather than glossed: an order's
    own items go through a station in sequence, so three sandwiches on a single
    ticket take three cycles. Baskets are almost always one or two items, and
    the saving measured across orders is unaffected — but a large single order
    is modelled pessimistically."""
    at_s = 11 * 3600
    arrival = Arrival(
        "c0", "o0", float(at_s), Channel.WALKUP,
        tuple(Line("bacon_egg_cheese_bagel", None, None) for _ in range(3)), "test",
    )
    result = run(batching, 0, arrivals=[arrival])

    cycles = [
        event for event in result.log
        if event.station == "panini_press" and event.type is EventType.STATION_END
    ]
    assert len(cycles) == 3
    assert all(event.payload["size"] == 1 for event in cycles)
