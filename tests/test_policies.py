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

from core.events import EventType
from core.menu import make_item, make_order
from core.params import ConfigError, load_params
from core.states import State
from core.types import Channel, Line
from sim.arrivals import Arrival
from sim.engine import run
from sim.policies import POLICIES, BatchPolicy, FIFOPolicy, Pending, make_policy

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
    limit = params.station("panini_press").batch_size
    batch = policy.next_batch("panini_press", queue, 0.0)
    assert len(batch) == limit
    assert batch == queue[:limit]


def test_batching_fills_the_pitcher_and_stops(params):
    policy = BatchPolicy(params)
    wand = params.station("steam_wand")
    queue = [
        _pending(params, "steam_wand", "latte", "oat", "hot", index=index)
        for index in range(6)
    ]
    batch = policy.next_batch("steam_wand", queue, 0.0)
    poured = sum(entry.item.tasks[0].oz for entry in batch)

    assert poured <= wand.max_batch_oz
    assert poured + batch[0].item.tasks[0].oz > wand.max_batch_oz    # one more will not fit
    assert len(batch) < len(queue)


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


def test_every_policy_the_config_allows_is_built():
    """A name the config accepts but nothing implements would quietly fall back
    to FIFO and report someone else's numbers."""
    from core.params import PolicyParams
    from typing import get_args

    allowed = set(get_args(PolicyParams.model_fields["name"].annotation))
    assert allowed == set(POLICIES), allowed ^ set(POLICIES)


def test_an_unknown_policy_fails_loudly(params):
    broken = params.model_copy(deep=True)
    object.__setattr__(broken.policy, "name", "cheapest_first")
    with pytest.raises(ConfigError, match="not implemented"):
        make_policy(broken)


def test_the_overlay_selects_the_batching_policy(params, batching):
    assert make_policy(params).name == "fifo"
    assert make_policy(batching).name == "batch_milk"


# --------------------------------------------------------------------------
# the engine actually runs them together
# --------------------------------------------------------------------------


def _sandwiches(params, count, at_s=None):
    # 11:00: three baristas on, so the registers clear together and the
    # sandwiches reach the press queue at the same moment
    at_s = 11 * 3600 if at_s is None else at_s
    return [
        Arrival(f"c{index}", f"o{index}", float(at_s), Channel.WALKUP,
                (Line("bacon_egg_cheese_bagel", None, None),), "test")
        for index in range(count)
    ]


def test_a_full_press_takes_one_cycle_per_sandwich_under_fifo(params):
    press = params.station("panini_press")
    count = press.batch_size
    result = run(params, 0, arrivals=_sandwiches(params, count))

    spans = [
        event.t_s for event in result.log
        if event.station == "panini_press" and event.type is EventType.STATION_END
    ]
    assert len(spans) == count
    assert max(spans) - min(spans) == pytest.approx((count - 1) * press.run_s)


def test_a_full_press_takes_one_cycle_when_batched(batching):
    """The fix: `batch_size: 3` now means something in the engine, not only in
    the cost model."""
    press = batching.station("panini_press")
    count = press.batch_size
    result = run(batching, 0, arrivals=_sandwiches(batching, count))

    ends = [
        event for event in result.log
        if event.station == "panini_press" and event.type is EventType.STATION_END
    ]
    assert len(ends) == 1
    assert ends[0].payload["size"] == count
    assert ends[0].payload["duration_s"] == press.run_s

    formed = [event for event in result.log if event.type is EventType.BATCH_FORMED]
    assert len(formed) == 1
    assert sorted(formed[0].payload["orders"]) == [f"o{index}" for index in range(count)]
    assert formed[0].payload["attended"] is False

    ready = [result.orders[f"o{index}"].entered_at(State.READY) for index in range(count)]
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
    at_s = batching.meta.start_s + 3600                     # 08:00, two baristas
    result = run(batching, 0, arrivals=_sandwiches(batching, 3, at_s=at_s))

    sizes = [
        event.payload["size"] for event in result.log
        if event.station == "panini_press" and event.type is EventType.STATION_END
    ]
    # whatever is queued when the press frees up goes in; the rest follows
    assert sum(sizes) == 3
    assert len(sizes) > 1
    assert all(size <= batching.station("panini_press").batch_size for size in sizes)


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


# --------------------------------------------------------------------------
# the same scheduler on both sides
# --------------------------------------------------------------------------


def test_the_scheduler_lives_in_core(request):
    """Ground rule 2: if the app and the simulator could disagree about what to
    make next, the logic is in the wrong place. `core` must stay importable
    without the simulator."""
    import ast

    source = (request.config.rootpath / "core" / "policies.py").read_text()
    imports = [
        name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        )
    ]
    assert "simpy" not in imports
    assert "sim" not in imports
    assert "app" not in imports


def test_the_simulator_and_the_bar_use_the_same_policy_object(batching):
    from core.policies import BatchPolicy
    from core.policies import make_policy as core_make
    from sim.policies import make_policy as sim_make

    assert sim_make is core_make
    assert isinstance(core_make(batching), BatchPolicy)


def test_the_plan_covers_every_waiting_item_exactly_once(params, batching):
    from core.policies import make_policy, plan_batches

    queue = [
        _pending(params, "panini_press", "bacon_egg_cheese_bagel", index=index)
        for index in range(5)
    ]
    for policy in (make_policy(params), make_policy(batching)):
        plan = plan_batches(policy, "panini_press", queue, 0.0)
        flattened = [entry for batch in plan for entry in batch]
        assert len(flattened) == len(queue)
        assert {id(entry) for entry in flattened} == {id(entry) for entry in queue}


def test_every_event_names_the_scheduler_in_force(params, batching):
    """A week of logs spanning two policies is uninterpretable without it."""
    assert {event.policy for event in run(params, 0).log} == {"fifo"}
    assert {event.policy for event in run(batching, 0).log} == {"batch_milk"}


# --------------------------------------------------------------------------
# M5 arm E: bounded reorder
# --------------------------------------------------------------------------

REORDER = "params/experiments/reorder.yaml"


@pytest.fixture(scope="module")
def reordering():
    return load_params(BASE, REORDER)


def test_a_changeover_needs_something_to_change_between(tmp_path):
    overlay = tmp_path / "bad.yaml"
    overlay.write_text("stations:\n  brew_tap: { changeover_s: 10 }\n")
    with pytest.raises(ConfigError, match="changeover_s"):
        load_params(BASE, overlay)


def test_reordering_prefers_work_that_needs_no_changeover(reordering):
    from core.policies import BoundedReorderPolicy

    policy = BoundedReorderPolicy(reordering)
    queue = [
        _pending(reordering, "steam_wand", "latte", "whole", "hot", at=0.0, index=0),
        _pending(reordering, "steam_wand", "latte", "oat", "hot", at=1.0, index=1),
    ]

    # nothing run yet, so no changeover to avoid: strict order
    assert policy.next_batch("steam_wand", queue, 2.0)[0].item.milk_type == "whole"
    # having just run whole, it now reaches past the oat drink for another whole
    queue.append(_pending(reordering, "steam_wand", "latte", "whole", "hot", at=2.0, index=2))
    assert policy.next_batch("steam_wand", queue[1:], 3.0)[0].item.milk_type == "whole"


def test_nobody_is_passed_over_past_the_guard(reordering):
    """The guard is what makes reordering safe to run on a real bar."""
    from core.policies import BoundedReorderPolicy

    policy = BoundedReorderPolicy(reordering)
    guard = reordering.policy.starvation_guard_s
    policy._last_key["steam_wand"] = "oat"

    stranded = _pending(reordering, "steam_wand", "latte", "whole", "hot", at=0.0, index=0)
    convenient = _pending(reordering, "steam_wand", "latte", "oat", "hot", at=10.0, index=1)
    queue = [stranded, convenient]

    # before the guard, the convenient one goes first
    assert policy.next_batch("steam_wand", queue, 20.0)[0] is convenient
    # past it, the stranded one jumps regardless of what it costs
    policy._last_key["steam_wand"] = "oat"
    assert policy.next_batch("steam_wand", queue, guard + 1.0)[0] is stranded


def test_reordering_never_delays_an_order_past_the_guard(params, reordering):
    """The plan's acceptance criterion, measured against FIFO on the same day."""
    guard = reordering.policy.starvation_guard_s
    for seed in (0, 1, 2):
        fifo = {
            order_id: order.entered_at(State.READY)
            for order_id, order in run(params, seed).orders.items()
        }
        reordered = {
            order_id: order.entered_at(State.READY)
            for order_id, order in run(reordering, seed).orders.items()
        }
        delayed = [
            reordered[order_id] - fifo[order_id]
            for order_id in fifo
            if fifo.get(order_id) is not None and reordered.get(order_id) is not None
        ]
        assert delayed
        assert max(delayed) <= guard, max(delayed)


def test_reordering_has_no_scope_on_this_cafes_demand(reordering):
    """Recorded because it is the answer, not because it is a happy one.

    A reordering policy can only act on a station with a queue to permute. The
    wand is idle almost every time it is asked for work, so there is nothing to
    reorder; the station that does back up has no changeover to avoid. If
    demand ever rises enough for that to stop being true, this fails and the
    arm becomes worth running.
    """
    from collections import Counter

    from core.policies import BoundedReorderPolicy
    from sim import engine as engine_module

    depths: Counter = Counter()

    class Watched(BoundedReorderPolicy):
        def next_batch(self, station, pending, now):
            depths[(station, len(pending))] += 1
            return super().next_batch(station, pending, now)

    original = engine_module.make_policy
    engine_module.make_policy = lambda p: Watched(p)
    try:
        for seed in range(4):
            run(reordering, seed)
    finally:
        engine_module.make_policy = original

    wand = {n: c for (station, n), c in depths.items() if station == "steam_wand"}
    assert sum(wand.values()) > 50
    assert wand.get(1, 0) / sum(wand.values()) > 0.85

    press = {n: c for (station, n), c in depths.items() if station == "panini_press"}
    assert press, "the press should be dispatching work"
    assert reordering.station("panini_press").batch_key is None
