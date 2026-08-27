"""M1 acceptance tests.

Done when: illegal transitions raise; batch_cost of 4 same-milk lattes is
strictly less than 4x cost; core imports nothing from app/ or sim/; a corrupted
base.yaml raises a validation error naming the bad field.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.capacity import StationCapacityModel, task_seconds
from core.events import EventLog, EventType
from core.menu import make_item, make_order, service_seconds
from core.params import ConfigError, load_params, params_from_dict, parse_hhmm
from core.states import LEGAL, LOST, TERMINAL, IllegalTransition, State, transition
from core.types import Channel, Item, Order, TaskKind

# --------------------------------------------------------------------------
# config: loads, validates, and fails loudly by name
# --------------------------------------------------------------------------


def test_base_config_loads(params):
    assert params.meta.scenario == "base_assumed"
    assert params.bottleneck_station in params.stations
    assert set(params.mix.drink) == set(params.menu)


def test_every_parameter_carries_provenance(params):
    report = params.provenance_report()
    assert report.total > 0
    assert report.assumed_fraction == 1.0
    assert report.caption() == "provenance: 100% assumed"
    assert params.source_of("stations.steam_wand.setup_s") == "assumed"


def test_overlay_flips_provenance_without_touching_code(tmp_path, root):
    overlay = tmp_path / "observed.yaml"
    overlay.write_text(
        "stations:\n"
        "  steam_wand: { setup_s: 7.5, per_6oz_s: 12.0, source: observed }\n"
        "arrivals:\n"
        "  capture_rate: 0.041\n"
        "  source: fitted\n"
    )
    merged = load_params(root / "params" / "base.yaml", overlay)

    assert merged.station("steam_wand").setup_s == 7.5
    assert merged.source_of("stations.steam_wand.setup_s") == "observed"
    assert merged.source_of("arrivals.capture_rate") == "fitted"
    assert merged.source_of("stations.group_head.shot_s") == "assumed"

    report = merged.provenance_report()
    assert 0.0 < report.assumed_fraction < 1.0
    assert report.counts["observed"] >= 2
    assert sum(report.counts.values()) == report.total


def test_unknown_field_is_named(corrupt):
    message = corrupt(lambda cfg: cfg["stations"]["steam_wand"].update(setup_seconds=10))
    assert "stations.steam_wand.setup_seconds" in message


def test_missing_field_is_named(corrupt):
    message = corrupt(lambda cfg: cfg["policy"].pop("starvation_guard_s"))
    assert "policy.starvation_guard_s" in message


def test_mix_that_does_not_sum_is_named(corrupt):
    message = corrupt(lambda cfg: cfg["mix"]["milk"].update(oat=0.9))
    assert "mix.milk" in message
    assert "sum to 1.0" in message


def test_menu_pointing_at_an_unknown_station_is_named(corrupt):
    message = corrupt(
        lambda cfg: cfg["menu"]["latte"]["tasks"].__setitem__(
            0, {"station": "steamer", "oz": 8}
        )
    )
    assert "menu.latte.tasks[0]" in message
    assert "steamer" in message


def test_bottleneck_must_be_a_real_station(corrupt):
    message = corrupt(lambda cfg: cfg.update(bottleneck_station="espresso"))
    assert "bottleneck_station" in message


def test_staffing_must_tile_the_day(corrupt):
    message = corrupt(lambda cfg: cfg["staffing"][1].update({"from": "10:30"}))
    assert "staffing" in message


def test_negative_duration_is_rejected(corrupt):
    message = corrupt(lambda cfg: cfg["stations"]["group_head"].update(shot_s=-1))
    assert "stations.group_head.shot_s" in message


def test_task_missing_a_required_dimension_is_named(corrupt):
    message = corrupt(lambda cfg: cfg["menu"]["latte"]["tasks"][0].pop("oz"))
    assert "menu.latte.tasks[0]" in message
    assert "oz" in message


def test_class_block_outside_the_sim_window_is_named(corrupt):
    message = corrupt(lambda cfg: cfg["arrivals"]["class_blocks"][0].update(ends_at="18:50"))
    assert "arrivals.class_blocks[0].ends_at" in message


def test_parameter_without_provenance_is_rejected(raw_base):
    import copy

    cfg = copy.deepcopy(raw_base)
    cfg["meta"].pop("source")
    with pytest.raises(ConfigError) as exc:
        params_from_dict(cfg)
    assert "no provenance" in str(exc.value)


def test_unknown_provenance_value_is_rejected(corrupt):
    message = corrupt(lambda cfg: cfg["policy"].update(source="guessed"))
    assert "policy.source" in message


def test_missing_file_is_loud(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_params(tmp_path / "nope.yaml")


def test_clock_parsing_is_strict():
    assert parse_hhmm("07:00") == 7 * 3600
    with pytest.raises(ConfigError):
        parse_hhmm("7:00")
    with pytest.raises(ConfigError):
        parse_hhmm("25:00")


def test_staffing_lookup(params):
    assert params.baristas_at(parse_hhmm("08:00")) == 2
    assert params.baristas_at(parse_hhmm("11:00")) == 3
    assert params.station_capacity("register", parse_hhmm("11:00")) == 3
    assert params.station_capacity("steam_wand", parse_hhmm("11:00")) == 1
    with pytest.raises(ConfigError):
        params.baristas_at(parse_hhmm("06:00"))


# --------------------------------------------------------------------------
# state machine
# --------------------------------------------------------------------------


def _order(params, drink="latte", milk="oat", **kwargs) -> Order:
    return make_order("o1", params, lines=[(drink, milk)], **kwargs)


def test_happy_path_emits_one_event_per_transition(params):
    log = EventLog("t", 1)
    order = _order(params)
    for state in (State.ACCEPTED, State.IN_PROGRESS, State.READY, State.PICKED_UP):
        transition(order, state, at=100.0, actor="barista", log=log)

    assert order.state is State.PICKED_UP
    assert len(log) == len(order.history) == 4
    assert all(event.type is EventType.STATE_CHANGE for event in log)
    assert [event.to_state for event in log] == [
        State.ACCEPTED,
        State.IN_PROGRESS,
        State.READY,
        State.PICKED_UP,
    ]
    assert log[0].from_state == State.PLACED
    assert log[0].channel == Channel.WALKUP


def test_illegal_transition_raises_and_changes_nothing(params):
    log = EventLog("t", 1)
    order = _order(params)
    with pytest.raises(IllegalTransition):
        transition(order, State.READY, at=1.0, log=log)
    assert order.state is State.PLACED
    assert len(log) == 0
    assert order.history == []


def test_terminal_states_are_absorbing(params):
    for terminal in TERMINAL:
        assert LEGAL[terminal] == set()
        order = _order(params)
        order.state = terminal
        for target in State:
            with pytest.raises(IllegalTransition):
                transition(order, target, at=1.0)


def test_every_state_appears_in_the_rulebook():
    assert set(LEGAL) == set(State)
    for onward in LEGAL.values():
        assert onward <= set(State)
    assert LOST <= TERMINAL
    assert State.PICKED_UP in TERMINAL and State.PICKED_UP not in LOST


def test_a_balk_is_reachable_and_final(params):
    order = _order(params)
    transition(order, State.BALKED, at=5.0, actor="customer", estimated_wait_s=612.0)
    assert order.state is State.BALKED
    assert order.history[-1].payload["estimated_wait_s"] == 612.0
    assert order.is_terminal


def test_entered_at_reads_the_event_trail(params):
    order = _order(params)
    transition(order, State.ACCEPTED, at=10.0)
    transition(order, State.IN_PROGRESS, at=25.0)
    assert order.entered_at(State.IN_PROGRESS) == 25.0
    assert order.entered_at(State.READY) is None


# --------------------------------------------------------------------------
# menu resolution
# --------------------------------------------------------------------------


def test_a_lone_latte_costs_exactly_the_formula(params):
    wand = params.station("steam_wand")
    head = params.station("group_head")
    spec = params.menu_item("latte")
    expected = wand.setup_s + wand.per_6oz_s * (8 / 6) + head.shot_s + spec.assembly_s

    item = make_item("latte", params, order_id="o1", item_id="o1-0", milk_type="oat")
    assert service_seconds(item) == pytest.approx(expected)


def test_station_plan_shape(params):
    item = make_item("latte", params, order_id="o1", item_id="o1-0", milk_type="oat")
    stations = [task.station for task in item.tasks]
    assert stations == ["steam_wand", "group_head", None]
    assert item.tasks[-1].kind is TaskKind.ASSEMBLY
    assert item.tasks[0].batch_value == "oat"      # batches on milk_type
    assert item.tasks[1].batch_value is None       # group head does not batch


def test_milk_rules_are_enforced(params):
    with pytest.raises(ConfigError):
        make_item("latte", params, order_id="o", item_id="i")            # milk missing
    with pytest.raises(ConfigError):
        make_item("drip", params, order_id="o", item_id="i", milk_type="oat")
    with pytest.raises(ConfigError):
        make_item("latte", params, order_id="o", item_id="i", milk_type="hemp")
    with pytest.raises(ConfigError):
        make_item("flat_white", params, order_id="o", item_id="i", milk_type="oat")


def test_order_totals(params):
    order = make_order("o1", params, lines=[("latte", "oat"), ("pastry", None)])
    assert order.price_cents == 500 + 350
    assert order.margin_cents == (500 - 130) + (350 - 110)
    assert [item.item_id for item in order.items] == ["o1-0", "o1-1"]
    assert len(order.milk_items) == 1


# --------------------------------------------------------------------------
# capacity: the batching gain is the whole research question
# --------------------------------------------------------------------------


def _lattes(params, n, milk="oat", order_id="o1"):
    return make_order(order_id, params, lines=[("latte", milk)] * n).items


def test_batching_four_same_milk_lattes_beats_making_them_one_by_one(params):
    model = StationCapacityModel(params)
    items = _lattes(params, 4)
    solo_total = sum(model.cost(item) for item in items)
    assert model.batch_cost(items) < solo_total
    # setup is paid once instead of four times
    assert solo_total - model.batch_cost(items) == pytest.approx(
        3 * params.station("steam_wand").setup_s
    )


def test_batch_cost_never_exceeds_the_sum(params):
    model = StationCapacityModel(params)
    for n in range(1, 9):
        items = _lattes(params, n)
        assert model.batch_cost(items) <= sum(model.cost(item) for item in items)
    assert model.batch_cost(_lattes(params, 1)) == model.cost(_lattes(params, 1)[0])


def test_the_batch_respects_the_pitcher(params):
    model = StationCapacityModel(params)
    wand = params.station("steam_wand")
    items = _lattes(params, 5)  # 40oz against a 32oz pitcher
    groups = model.group(items)
    assert [len(group) for group in groups] == [4, 1]
    assert model.batch_cost(items) == pytest.approx(
        2 * wand.setup_s + wand.per_6oz_s * (40 / 6)
    )


def test_different_milks_do_not_batch(params):
    model = StationCapacityModel(params)
    mixed = make_order("o1", params, lines=[("latte", "oat"), ("latte", "whole")]).items
    assert model.batch_cost(mixed) == sum(model.cost(item) for item in mixed)
    assert {group.key for group in model.group(mixed)} == {"oat", "whole"}


def test_items_that_miss_the_bottleneck_are_free(params):
    model = StationCapacityModel(params)
    drip = make_item("drip", params, order_id="o1", item_id="o1-0")
    assert model.cost(drip) == 0.0
    assert model.group([drip]) == []


def test_oven_batches_by_count(params):
    model = StationCapacityModel(params, station_name="oven")
    oven = params.station("oven")
    items = make_order("o1", params, lines=[("pastry", None)] * 7).items
    assert [len(group) for group in model.group(items)] == [6, 1]
    assert model.batch_cost(items) == pytest.approx(2 * oven.run_s)


def test_register_bottleneck_degenerates_to_order_counts(root, tmp_path):
    """If M8 finds the register is the constraint, this is a YAML edit."""
    overlay = tmp_path / "register.yaml"
    overlay.write_text("bottleneck_station: register\n")
    params = load_params(root / "params" / "base.yaml", overlay)
    model = StationCapacityModel(params)
    base_s = params.station("register").base_s

    one_order = make_order("o1", params, lines=[("latte", "oat")] * 4).items
    assert model.batch_cost(one_order) == base_s          # one order, one interaction
    assert sum(model.cost(item) for item in one_order) == 4 * base_s

    two_orders = one_order + make_order("o2", params, lines=[("drip", None)]).items
    assert model.batch_cost(two_orders) == 2 * base_s


def test_capacity_seconds_follows_staffing(params, root, tmp_path):
    model = StationCapacityModel(params)
    assert model.capacity_seconds(300.0) == 300.0  # one steam wand

    overlay = tmp_path / "register.yaml"
    overlay.write_text("bottleneck_station: register\n")
    register = StationCapacityModel(load_params(root / "params" / "base.yaml", overlay))
    assert register.capacity_seconds(300.0, parse_hhmm("11:00")) == 900.0
    assert register.capacity_seconds(300.0, parse_hhmm("08:00")) == 600.0


def test_task_seconds_ignores_order_terms_by_default(params):
    register = params.station("register")
    assert task_seconds(register) == 0.0
    assert task_seconds(register, include_order_terms=True) == register.base_s


# --------------------------------------------------------------------------
# event log
# --------------------------------------------------------------------------


def test_event_ids_are_deterministic_not_random(params):
    def run() -> EventLog:
        log = EventLog("base_assumed", 42, is_simulated=True)
        order = make_order("o1", params, lines=[("latte", "oat")], is_simulated=True)
        for state in (State.ACCEPTED, State.IN_PROGRESS, State.READY):
            transition(order, state, at=100.0, actor="barista", log=log)
        log.emit(EventType.STATION_START, 100.0, station="steam_wand", order_id="o1")
        return log

    first, second = run(), run()
    assert first.digest() == second.digest()
    assert [event.event_id for event in first] == [
        "base_assumed:42:0000000",
        "base_assumed:42:0000001",
        "base_assumed:42:0000002",
        "base_assumed:42:0000003",
    ]
    assert EventLog("base_assumed", 43).digest() != first.digest()


def test_log_roundtrips_through_jsonl(tmp_path, params):
    log = EventLog("base_assumed", 42, is_simulated=True)
    order = make_order("o1", params, lines=[("latte", "oat")], is_simulated=True)
    transition(order, State.ACCEPTED, at=100.0, log=log, note="hand test")
    path = log.write_jsonl(tmp_path / "events.jsonl")

    reloaded = EventLog.read_jsonl(path)
    assert reloaded.digest() == log.digest()
    assert reloaded[0].payload == {"note": "hand test"}
    assert reloaded[0].is_simulated is True


def test_events_are_immutable(params):
    log = EventLog("t", 1)
    order = _order(params)
    event = transition(order, State.ACCEPTED, at=1.0, log=log)
    with pytest.raises(Exception):
        event.t_s = 2.0


# --------------------------------------------------------------------------
# ground rule 2: core is the shared floor and imports neither storey
# --------------------------------------------------------------------------


def test_core_imports_nothing_from_app_or_sim(root):
    forbidden = {"app", "sim", "analysis", "fastapi", "sqlmodel", "simpy", "web"}
    offences: list[str] = []
    for path in sorted((root / "core").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.split(".")[0] in forbidden:
                    offences.append(f"{path.name}:{node.lineno} imports {name}")
    assert offences == []


def test_core_has_no_magic_numbers(root):
    """Ground rule 1: durations and rates come from config, not source."""
    allowed = {0, 1, 2, 6, 60, 100, 256}  # indices, sha width, percent, oz-per-unit
    offences: list[str] = []
    for path in sorted((root / "core").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                if isinstance(node.value, bool):
                    continue
                if node.value in allowed or 0 < node.value < 1e-3:
                    continue
                offences.append(f"{path.name}:{node.lineno} -> {node.value}")
    assert offences == [], "\n".join(offences)


def test_placement_is_an_event_with_no_from_state(params):
    log = EventLog("t", 1)
    order = _order(params)
    from core.states import place

    event = place(order, at=42.0, log=log)
    assert event.from_state is None
    assert event.to_state == State.PLACED
    assert order.state is State.PLACED
    assert order.entered_at(State.PLACED) == 42.0

    transition(order, State.ACCEPTED, at=50.0, log=log)
    with pytest.raises(IllegalTransition):
        place(order, at=60.0, log=log)
    assert len(log) == 2


def test_a_live_log_does_not_unflag_a_simulated_order(params):
    log = EventLog("live", None, is_simulated=False)
    real = make_order("o1", params, lines=[("drip", None)])
    fake = make_order("o2", params, lines=[("drip", None)], is_simulated=True)
    transition(real, State.ACCEPTED, at=1.0, log=log)
    transition(fake, State.ACCEPTED, at=1.0, log=log)
    assert [event.is_simulated for event in log] == [False, True]
