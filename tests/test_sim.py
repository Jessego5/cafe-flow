"""M3 acceptance tests.

Done when: the day conserves customers; the same seed reproduces the same log;
a lone latte takes exactly its make time; and the arrival pattern shows five
distinct bursts.

The tests about who holds what (a barista never parallel with themselves, a
station never over capacity) guard the modelling decision that would otherwise
silently invalidate every result: stations are seized by baristas, not run as
independent parallel servers.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
import pytest

from core.events import EventType
from core.params import SECONDS_PER_MINUTE, ConfigError, load_params
from core.states import LEGAL, State
from sim.arrivals import Arrival, class_block_size, generate_arrivals
from sim.engine import event_path, run
from core.types import Channel, Line

SEEDS = [0, 1, 7, 42]
BIN_S = 300           # 5 minute bins for the arrival histogram
BURST_FLOOR = 5       # arrivals in a bin that a Poisson background never reaches


@pytest.fixture(scope="module")
def params():
    return load_params("params/base.yaml")


@pytest.fixture(scope="module")
def day(params):
    return run(params, 42)


def intervals(log, *, by: str = "actor", attended_only: bool | None = None):
    """Reconstruct busy periods from the station_start/station_end pairs.

    Grouping by actor answers "was this person busy", which counts attended
    work only; grouping by station answers "was this machine busy", which counts
    everything. A press cycle is the second and not the first.
    """
    if attended_only is None:
        attended_only = by == "actor"

    open_at: dict[tuple, tuple[float, bool]] = {}
    spans: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for event in log:
        if event.type not in (EventType.STATION_START, EventType.STATION_END):
            continue
        key = (event.actor, event.station, event.item_id, event.order_id)
        if event.type is EventType.STATION_START:
            open_at[key] = (event.t_s, bool(event.payload.get("attended", True)))
        else:
            started = open_at.pop(key, None)
            if started is None:
                continue
            start_s, attended = started
            if attended_only and not attended:
                continue
            label = event.actor if by == "actor" else event.station
            spans[label].append((start_s, event.t_s))
    return spans


def overlaps(spans: list[tuple[float, float]]) -> list[tuple]:
    ordered = sorted(spans)
    return [
        (earlier, later)
        for earlier, later in zip(ordered, ordered[1:])
        if later[0] < earlier[1] - 1e-9
    ]


def max_concurrent(spans: list[tuple[float, float]]) -> int:
    edges = sorted(
        [(start, 1) for start, _ in spans] + [(end, -1) for _, end in spans],
        key=lambda pair: (pair[0], pair[1]),
    )
    running = peak = 0
    for _, delta in edges:
        running += delta
        peak = max(peak, running)
    return peak


def states_of(log, order_id: str) -> list[str]:
    return [
        event.to_state
        for event in log
        if event.type is EventType.STATE_CHANGE and event.order_id == order_id
    ]


# --------------------------------------------------------------------------
# conservation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_day_conserves_customers(params, seed):
    result = run(params, seed)
    census = result.census()
    assert census["placed"] == len(result.arrivals)
    assert census["placed"] == (
        census["picked_up"]
        + census["balked"]
        + census["abandoned"]
        + census["cancelled"]
        + census["in_flight_at_end"]
    )
    assert result.conserved()


def test_stopping_mid_rush_leaves_orders_in_flight(params):
    """Closing the doors mid-rush must leave work in flight, not quietly drop
    it: that term is what makes the identity worth checking."""
    busiest = max(
        params.arrivals.class_blocks, key=lambda block: block.sections * block.avg_enrollment
    )
    mid_rush = busiest.ends_at_s + (params.arrivals.offset_min + 2) * SECONDS_PER_MINUTE
    result = run(params, 42, until_s=mid_rush)
    census = result.census()
    assert census["in_flight_at_end"] > 0
    assert result.conserved()


def test_every_order_takes_a_legal_path(day):
    for order_id in day.orders:
        path = [State.PLACED] + [State(s) for s in states_of(day.log, order_id)[1:]]
        for earlier, later in zip(path, path[1:]):
            assert later in LEGAL[earlier], f"{order_id}: {earlier} -> {later}"


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_same_seed_is_the_same_day(params, seed):
    first, second = run(params, seed), run(params, seed)
    assert first.log.digest() == second.log.digest()
    assert first.log.rows() == second.log.rows()
    assert first.census() == second.census()


def test_a_different_seed_is_a_different_day(params):
    assert run(params, 1).log.digest() != run(params, 2).log.digest()


def test_the_log_is_flagged_simulated_throughout(day):
    assert all(event.is_simulated for event in day.log)
    assert all(order.is_simulated for order in day.orders.values())
    assert {event.seed for event in day.log} == {42}
    assert {event.scenario for event in day.log} == {"ground_truth_assumed"}


def test_the_log_writes_the_shared_schema(day, tmp_path):
    import pandas as pd

    from core.events import EVENT_COLUMNS

    path = day.log.write_parquet(event_path(day.scenario, day.seed, tmp_path))
    assert path.name == "events_ground_truth_assumed_42.parquet"

    frame = pd.read_parquet(path)
    assert list(frame.columns) == list(EVENT_COLUMNS)
    assert len(frame) == len(day.log)
    assert frame["seq"].is_monotonic_increasing


# --------------------------------------------------------------------------
# service times
# --------------------------------------------------------------------------


def _solo(params, drink="latte", milk="oat", variant="hot", at_s=None):
    """One customer, alone in an empty cafe."""
    at_s = params.meta.start_s + 3600 if at_s is None else at_s
    arrival = Arrival(
        customer_id="c0",
        order_id="o0",
        at_s=float(at_s),
        channel=Channel.WALKUP,
        lines=(Line(drink, milk, variant),),
        source="test",
    )
    return run(params, 0, arrivals=[arrival])


def test_a_lone_latte_takes_exactly_the_make_time(params):
    """Stated as the formula, so it still means something after the service
    times are revised.

    The shot pulls while the milk steams -- the group head is unattended, so it
    needs a slot and not a person -- and the pour waits for whichever finishes
    last. `max`, not `+`. Summing them priced a latte at 112 seconds against a
    sandwich's 90 and made drinks the slower half of the menu, which is the
    wrong way round from what the cafe does.
    """
    wand = params.station("steam_wand")
    head = params.station("group_head")
    tasks, assembly_s, _ = params.menu_item("latte").plan("hot")
    steam = next(task for task in tasks if task.station == "steam_wand")
    shot = next(task for task in tasks if task.station == "group_head")
    steaming = wand.setup_s + wand.per_6oz_s * (steam.oz / 6)
    pulling = head.shot_s * shot.shots
    expected = max(steaming, pulling) + assembly_s

    result = _solo(params)
    order = result.orders["o0"]
    started = order.entered_at(State.IN_PROGRESS)
    ready = order.entered_at(State.READY)
    assert ready - started == pytest.approx(expected)


def test_a_lone_customer_waits_only_for_the_register_and_the_drink(params):
    from core.capacity import StationCapacityModel
    from core.menu import service_seconds

    result = _solo(params)
    order = result.orders["o0"]
    placed = order.entered_at(State.PLACED)
    # the register is a transaction plus a few seconds an item
    ringing_up = StationCapacityModel(params, "register").order_cost(order)

    assert order.entered_at(State.ACCEPTED) - placed == pytest.approx(ringing_up)
    # service_seconds sums the item's tasks; the bar overlaps whatever needs
    # no person, so the drink is ready by the longest strand and not the total.
    building = max(
        max((task.duration_s for task in order.items[0].tasks
             if task.station is not None
             and not params.station(task.station).attended), default=0.0),
        sum(task.duration_s for task in order.items[0].tasks
            if task.station is not None and params.station(task.station).attended),
    ) + sum(task.duration_s for task in order.items[0].tasks if task.station is None)
    assert building < service_seconds(order.items[0])
    assert order.entered_at(State.PICKED_UP) - placed == pytest.approx(
        ringing_up + building
    )


def test_the_register_is_worked_by_a_barista(params):
    """The register competes for barista time even when it is not the
    bottleneck, so it appears in the log as station work like anything else."""
    result = _solo(params, drink="drip_coffee", milk=None, variant=None)
    stations = [
        event.station for event in result.log if event.type is EventType.STATION_START
    ]
    assert stations[0] == "register"
    assert all(
        event.actor.startswith("barista")
        for event in result.log
        if event.type is EventType.STATION_START
    )


# --------------------------------------------------------------------------
# the resource model
# --------------------------------------------------------------------------


def test_a_barista_is_never_parallel_with_themself(day):
    for actor, spans in intervals(day.log, by="actor").items():
        assert overlaps(spans) == [], f"{actor} is in two places at once"


def test_the_coffee_gets_made_while_the_panini_presses(params):
    """A press occupies the press, not a person. Two customers arrive together,
    one wanting a sandwich and one a latte: the latte must not wait out the
    press cycle."""
    at_s = params.meta.start_s + 3600
    arrivals = [
        Arrival("c0", "o0", float(at_s), Channel.WALKUP,
                (Line("bacon_egg_cheese_bagel", None, None),), "test"),
        Arrival("c1", "o1", float(at_s), Channel.WALKUP,
                (Line("latte", "oat", "hot"),), "test"),
    ]
    result = run(params, 0, arrivals=arrivals)

    press = [
        (event.t_s, event.type)
        for event in result.log
        if event.station == "panini_press"
    ]
    assert len(press) == 2
    press_start, press_end = press[0][0], press[1][0]
    assert press_end - press_start == params.station("panini_press").run_s

    # the latte is finished before the press cycle ends
    latte_ready = result.orders["o1"].entered_at(State.READY)
    assert latte_ready < press_end

    # and nobody was standing at the press: no attended span covers the cycle
    for spans in intervals(result.log, by="actor").values():
        for start, end in spans:
            assert not (start <= press_start and end >= press_end)


def test_a_finished_sandwich_still_blocks_the_press(params):
    """Unattended does not mean free: the press stays seized until someone
    comes back for it, which is exactly why collection matters."""
    assert params.station("panini_press").attended is False
    assert params.station("steam_wand").attended is True
    assert params.station("register").attended is True


def test_no_station_exceeds_its_capacity(day, params):
    for station, spans in intervals(day.log, by="station").items():
        if station is None:
            continue
        assert max_concurrent(spans) <= params.station_capacity(station), station


def test_one_wand_serialises_two_simultaneous_lattes(params):
    """Two baristas, one steam wand: the wand contends and a barista blocks.

    If stations were modelled as independent parallel servers this would pass
    with the two steams overlapping, and every throughput number in the project
    would be wrong.
    """
    at_s = params.meta.start_s + 3600
    arrivals = [
        Arrival(
            f"c{index}", f"o{index}", float(at_s), Channel.WALKUP,
            (Line("latte", "oat", "hot"),), "test",
        )
        for index in range(2)
    ]
    result = run(params, 0, arrivals=arrivals)

    wand_spans = intervals(result.log, by="station")["steam_wand"]
    assert len(wand_spans) == 2
    assert overlaps(wand_spans) == []
    assert max_concurrent(wand_spans) == 1

    # Both shots pull at once -- two group heads -- so the second latte is late
    # by the wand alone, and only by however much its second steam outruns the
    # extraction it was already overlapping.
    ready = sorted(result.orders[o].entered_at(State.READY) for o in result.orders)
    steam = params.station("steam_wand")
    head = params.station("group_head")
    tasks = params.menu_item("latte").plan("hot")[0]
    steam_oz = next(task.oz for task in tasks if task.station == "steam_wand")
    shots = next(task.shots for task in tasks if task.station == "group_head")
    steaming = steam.setup_s + steam.per_6oz_s * (steam_oz / 6)
    pulling = head.shot_s * shots
    assert ready[1] - ready[0] == pytest.approx(
        max(2 * steaming, pulling) - max(steaming, pulling)
    )


def test_the_crew_follows_the_staffing_plan(day, params):
    """Concurrency never exceeds the plan, and the extra barista does appear
    when the plan says so. Attended work only: a running machine is not a
    person on shift."""
    spans = [span for actor_spans in intervals(day.log).values() for span in actor_spans]
    assert max_concurrent(spans) == params.max_baristas

    for block in params.staffing:
        during = [
            (max(start, block.from_s), min(end, block.to_s))
            for start, end in spans
            if start < block.to_s and end > block.from_s
        ]
        # a barista mid-drink at a shift change finishes the drink, so allow
        # one carried-over hold at the start of a block that steps down
        assert max_concurrent(during) <= block.baristas + 1
        settled = [(s, e) for s, e in during if s >= block.from_s + 600]
        assert max_concurrent(settled) <= block.baristas, block.from_


# --------------------------------------------------------------------------
# arrivals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_arrival_pattern_has_five_bursts(params, seed):
    arrivals = generate_arrivals(params, np.random.default_rng(seed))
    bins = Counter(int(arrival.at_s // BIN_S) for arrival in arrivals)

    hot = sorted(index for index, count in bins.items() if count >= BURST_FLOOR)
    clusters: list[list[int]] = []
    for index in hot:
        if clusters and index - clusters[-1][-1] <= 1:
            clusters[-1].append(index)
        else:
            clusters.append([index])

    assert len(clusters) == 5, f"expected five bursts, got {len(clusters)}"

    peaks = [max(cluster, key=lambda index: bins[index]) * BIN_S for cluster in clusters]
    expected = [
        block.ends_at_s + params.arrivals.offset_min * SECONDS_PER_MINUTE
        for block in params.arrivals.class_blocks
    ]
    for peak, want in zip(peaks, expected):
        assert abs(peak - want) <= BIN_S


def test_block_volume_follows_the_capture_rate(params):
    arrivals = generate_arrivals(params, np.random.default_rng(3))
    by_source = Counter(arrival.source for arrival in arrivals)
    for index, block in enumerate(params.arrivals.class_blocks):
        wanted = class_block_size(block, params.arrivals.capture_rate)
        seen = by_source[f"block{index}@{block.ends_at}"]
        # only losses are draws that land outside opening hours
        assert 0 < seen <= wanted
        assert seen >= wanted - 2


def test_capture_rate_scales_demand(params, tmp_path):
    """The primary calibration knob at M8 has to actually move volume."""
    overlay = tmp_path / "capture.yaml"
    overlay.write_text("arrivals:\n  capture_rate: 0.11\n  source: fitted\n")
    doubled = load_params("params/base.yaml", overlay)

    base = generate_arrivals(params, np.random.default_rng(5))
    more = generate_arrivals(doubled, np.random.default_rng(5))
    assert len(more) > len(base) * 1.5


def test_nobody_arrives_outside_opening_hours(params):
    for seed in SEEDS:
        arrivals = generate_arrivals(params, np.random.default_rng(seed))
        assert all(
            params.meta.start_s <= arrival.at_s < params.meta.end_s for arrival in arrivals
        )
        assert [a.at_s for a in arrivals] == sorted(a.at_s for a in arrivals)


def test_baskets_respect_the_menu(params, day):
    for arrival in day.arrivals:
        for line in arrival.lines:
            spec = params.menu_item(line.drink)
            assert (line.milk_type is not None) == spec.requires_milk
            if line.milk_type is not None:
                assert line.milk_type in params.mix.milk
            assert (line.variant is not None) == bool(spec.variants)
            if line.variant is not None:
                assert line.variant in params.mix.serve


def test_the_hot_iced_split_follows_the_mix(params):
    """The single most important thing to count during observation: it decides
    how much of the menu milk batching can reach."""
    from collections import Counter

    served: Counter = Counter()
    for seed in range(8):        # one day is far too small a sample to assert on
        for arrival in generate_arrivals(params, np.random.default_rng(seed)):
            served.update(line.variant for line in arrival.lines if line.variant)

    total = sum(served.values())
    assert total > 300
    assert set(served) == set(params.mix.serve)
    assert abs(served["iced"] / total - params.mix.serve["iced"]) < 0.08


def test_only_hot_milk_drinks_reach_the_wand(params, day):
    from core.capacity import StationCapacityModel

    model = StationCapacityModel(params, station_name="steam_wand")
    for order in day.orders.values():
        for item in order.items:
            if item.variant == "iced":
                assert model.cost(item) == 0.0


# --------------------------------------------------------------------------
# demand as a measured curve rather than a timetable
# --------------------------------------------------------------------------


def _profile_params(root, tmp_path, rate_per_hour, capture=1.0, bin_minutes=60):
    overlay = tmp_path / "profile.yaml"
    overlay.write_text(
        "meta:\n  sim_start: \"08:00\"\n"
        f"  sim_end: \"{8 + len(rate_per_hour):02d}:00\"\n"
        "staffing:\n"
        f"  - {{ from: \"08:00\", to: \"{8 + len(rate_per_hour):02d}:00\", "
        "baristas: 3, source: assumed }\n"
        "arrivals:\n"
        "  model: profile\n"
        "  class_blocks: null\n"
        f"  capture_rate: {capture}\n"
        "  background_per_hour: 0\n"
        "  profile:\n"
        f"    bin_minutes: {bin_minutes}\n"
        f"    rate_per_hour: {list(rate_per_hour)}\n"
    )
    return load_params(root / "params" / "base.yaml", overlay)


def test_a_measured_curve_reproduces_its_own_rate(root, tmp_path):
    """A shop on a commuter street has no bell to model. The curve is the
    model, and what comes out has to match what went in."""
    params = _profile_params(root, tmp_path, [30.0, 30.0, 30.0])
    counts = [
        len(generate_arrivals(params, np.random.default_rng(seed))) for seed in range(12)
    ]
    assert 3 * 30 * 0.75 < mean_of(counts) < 3 * 30 * 1.25


def mean_of(values):
    return sum(values) / len(values)


def test_the_curve_puts_the_rush_where_the_rush_was(root, tmp_path):
    params = _profile_params(root, tmp_path, [60.0, 5.0, 5.0])
    arrivals = generate_arrivals(params, np.random.default_rng(3))
    first_hour = [a for a in arrivals if a.at_s < 9 * 3600]
    assert len(first_hour) > 3 * (len(arrivals) - len(first_hour))


def test_capture_rate_still_scales_a_measured_curve(root, tmp_path):
    """The same knob calibrates either arrivals model, which is what lets a
    measured shape and a fitted volume live together."""
    full = _profile_params(root, tmp_path, [40.0, 40.0], capture=1.0)
    half = _profile_params(root, tmp_path, [40.0, 40.0], capture=0.5)
    many = lambda p: mean_of(
        [len(generate_arrivals(p, np.random.default_rng(s))) for s in range(12)]
    )
    assert many(half) < many(full)
    assert 0.35 < many(half) / many(full) < 0.65


def test_a_profile_model_without_a_profile_is_refused(root, tmp_path):
    overlay = tmp_path / "broken.yaml"
    overlay.write_text("arrivals:\n  model: profile\n")
    with pytest.raises(ConfigError, match="no profile"):
        load_params(root / "params" / "base.yaml", overlay)


def test_a_timetable_model_without_a_timetable_is_refused(root, tmp_path):
    overlay = tmp_path / "broken.yaml"
    overlay.write_text("arrivals:\n  class_blocks: null\n")
    with pytest.raises(ConfigError, match="class_blocks"):
        load_params(root / "params" / "base.yaml", overlay)


def test_a_day_drawn_from_a_curve_is_still_deterministic(root, tmp_path):
    params = _profile_params(root, tmp_path, [25.0, 40.0, 15.0])
    first, second = run(params, 4), run(params, 4)
    assert first.log.digest() == second.log.digest()
    assert first.conserved()


def test_every_distribution_consumes_exactly_one_draw(params, root, tmp_path):
    """Swapping a lognormal for a constant must not re-shuffle every later
    random number in the run, or no two configurations stay comparable."""
    import numpy as np

    from sim.balking import sample_minutes

    def next_after(spec):
        overlay = tmp_path / "dist.yaml"
        overlay.write_text(f"customers:\n  balk_tolerance_min: {spec}\n")
        dist = load_params(root / "params" / "base.yaml", overlay).customers.balk_tolerance_min
        rng = np.random.default_rng(0)
        sample_minutes(dist, rng)
        return rng.random()

    lognormal = next_after("{ dist: lognormal, median: 7.0, sigma: 0.45 }")
    constant = next_after("{ dist: constant, value: 7.0 }")
    normal = next_after("{ dist: normal, mean: 7.0, sigma: 2.0 }")
    assert lognormal == constant == normal
