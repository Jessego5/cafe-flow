"""Metrics tested against hand-built logs with known answers.

Every function here reads the event log and nothing else (ground rule 4), so
each test states the log in full and then states the arithmetic it implies.
"""

from __future__ import annotations

import ast

import pytest

from analysis.metrics import (
    Interval,
    busiest_window,
    crew_utilisation,
    order_waits,
    peak_throughput,
    state_counts,
    station_busy_seconds,
    station_utilisation,
    throughput,
    wait_percentiles,
)
from core.events import EventLog, EventType
from core.params import load_params
from core.states import State


@pytest.fixture(scope="module")
def params():
    return load_params("params/base.yaml")


def _order(log, order_id, placed, ready, *, channel="walkup", picked_up=None):
    log.emit(EventType.STATE_CHANGE, placed, order_id=order_id, from_state=None,
             to_state=State.PLACED, channel=channel)
    log.emit(EventType.STATE_CHANGE, ready, order_id=order_id, from_state=State.IN_PROGRESS,
             to_state=State.READY, channel=channel)
    if picked_up is not None:
        log.emit(EventType.STATE_CHANGE, picked_up, order_id=order_id,
                 from_state=State.READY, to_state=State.PICKED_UP, channel=channel)


def _work(log, station, actor, start, end, order_id="o", item_id="i"):
    log.emit(EventType.STATION_START, start, station=station, actor=actor,
             order_id=order_id, item_id=item_id)
    log.emit(EventType.STATION_END, end, station=station, actor=actor,
             order_id=order_id, item_id=item_id)


# --------------------------------------------------------------------------
# waiting
# --------------------------------------------------------------------------


def test_waits_are_measured_from_the_log():
    log = EventLog("hand", 1)
    _order(log, "a", placed=100.0, ready=160.0)     # 60s
    _order(log, "b", placed=200.0, ready=500.0)     # 300s
    _order(log, "c", placed=300.0, ready=400.0)     # 100s

    assert order_waits(log) == {"a": 60.0, "b": 300.0, "c": 100.0}
    stats = wait_percentiles(log, percentiles=(50,))
    assert stats["n"] == 3
    assert stats["p50"] == 100.0
    assert stats["max"] == 300.0
    assert stats["mean"] == pytest.approx((60 + 300 + 100) / 3)


def test_an_unfinished_order_is_absent_not_zero():
    """A wait that has not finished is not a short wait."""
    log = EventLog("hand", 1)
    _order(log, "a", placed=100.0, ready=160.0)
    log.emit(EventType.STATE_CHANGE, 200.0, order_id="b", from_state=None,
             to_state=State.PLACED, channel="walkup")

    assert set(order_waits(log)) == {"a"}
    assert wait_percentiles(log)["n"] == 1


def test_waits_split_by_channel():
    log = EventLog("hand", 1)
    _order(log, "a", placed=0.0, ready=100.0, channel="walkup")
    _order(log, "b", placed=0.0, ready=300.0, channel="walkup")
    _order(log, "c", placed=0.0, ready=40.0, channel="preorder")

    split = wait_percentiles(log, percentiles=(50,), by_channel=True)
    assert split["walkup"]["p50"] == 200.0
    assert split["preorder"]["p50"] == 40.0
    assert split["preorder"]["n"] == 1


def test_the_window_selects_on_when_the_order_was_placed():
    """A rush is measured by the people who joined it, not by when their drink
    happened to land."""
    log = EventLog("hand", 1)
    _order(log, "early", placed=100.0, ready=140.0)
    _order(log, "inside", placed=1100.0, ready=3000.0)

    stats = wait_percentiles(log, window=Interval(1000.0, 2000.0), percentiles=(50,))
    assert stats["n"] == 1
    assert stats["p50"] == 1900.0


# --------------------------------------------------------------------------
# throughput
# --------------------------------------------------------------------------


def test_throughput_counts_completions_per_hour():
    log = EventLog("hand", 1)
    for index in range(3):                       # three in the first hour
        _order(log, f"a{index}", placed=0.0, ready=100.0 + index)
    for index in range(5):                       # five in the second
        _order(log, f"b{index}", placed=0.0, ready=3700.0 + index)

    series = throughput(log)
    assert [rate for _, rate in series] == [3.0, 5.0]
    assert peak_throughput(log) == 5.0


def test_a_half_hour_window_is_scaled_to_an_hourly_rate():
    log = EventLog("hand", 1)
    for index in range(4):
        _order(log, f"a{index}", placed=0.0, ready=10.0 + index)
    assert peak_throughput(log, window_s=1800.0) == 8.0


def test_throughput_of_an_empty_log_is_zero():
    assert throughput(EventLog("hand", 1)) == []
    assert peak_throughput(EventLog("hand", 1)) == 0.0


# --------------------------------------------------------------------------
# stations and crew
# --------------------------------------------------------------------------


def test_station_busy_seconds_sum_the_spans():
    log = EventLog("hand", 1)
    _work(log, "steam_wand", "barista-0", 0.0, 30.0, item_id="i1")
    _work(log, "steam_wand", "barista-1", 40.0, 60.0, item_id="i2")
    _work(log, "group_head", "barista-0", 30.0, 55.0, item_id="i1")

    assert station_busy_seconds(log) == {"steam_wand": 50.0, "group_head": 25.0}


def test_a_window_clips_work_that_straddles_it():
    log = EventLog("hand", 1)
    _work(log, "steam_wand", "barista-0", 90.0, 130.0)
    assert station_busy_seconds(log, window=Interval(100.0, 200.0)) == {"steam_wand": 30.0}


def test_utilisation_divides_by_capacity(params):
    log = EventLog("hand", 1)
    # one hour of a two-hour window, at a station with two group heads
    _work(log, "group_head", "barista-0", 0.0, 3600.0)
    window = Interval(0.0, 7200.0)

    utilisation = station_utilisation(log, params, window=window)
    assert utilisation["group_head"] == pytest.approx(3600 / (7200 * 2))


def test_assembly_has_no_station_and_is_reported_separately():
    log = EventLog("hand", 1)
    log.emit(EventType.STATION_START, 0.0, station=None, actor="barista-0",
             order_id="o", item_id="i")
    log.emit(EventType.STATION_END, 20.0, station=None, actor="barista-0",
             order_id="o", item_id="i")
    assert station_busy_seconds(log) == {None: 20.0}


def test_crew_utilisation_counts_every_hand(params):
    log = EventLog("hand", 1)
    eight_am = 8 * 3600.0
    # two baristas on shift at 08:00; one of them busy for the whole hour
    _work(log, "brew_tap", "barista-0", eight_am, eight_am + 3600.0)
    window = Interval(eight_am, eight_am + 3600.0)

    assert params.baristas_at(window.midpoint_s) == 2
    assert crew_utilisation(log, params, window=window) == pytest.approx(0.5)


# --------------------------------------------------------------------------
# windows and census
# --------------------------------------------------------------------------


def test_the_busiest_window_is_found_in_the_log():
    log = EventLog("hand", 1)
    for index in range(2):
        _order(log, f"quiet{index}", placed=float(index), ready=10.0)
    for index in range(9):
        _order(log, f"rush{index}", placed=20000.0 + index, ready=21000.0)

    window = busiest_window(log, length_s=3600.0, step_s=300.0)
    assert window is not None
    assert window.holds(20000.0)
    assert window.length_s == 3600.0
    assert busiest_window(EventLog("hand", 1)) is None


def test_state_counts_include_what_never_finished():
    log = EventLog("hand", 1)
    _order(log, "a", placed=0.0, ready=10.0, picked_up=20.0)
    _order(log, "b", placed=0.0, ready=10.0)          # still on the shelf

    counts = state_counts(log)
    assert counts["placed"] == 2
    assert counts["picked_up"] == 1
    assert counts["in_flight"] == 1


# --------------------------------------------------------------------------
# ground rule 2
# --------------------------------------------------------------------------


def test_analysis_imports_neither_runtime(request):
    forbidden = {"app", "sim", "fastapi", "sqlmodel", "simpy"}
    offences: list[str] = []
    for path in sorted((request.config.rootpath / "analysis").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            offences += [
                f"{path.name}:{node.lineno} imports {name}"
                for name in names
                if name.split(".")[0] in forbidden
            ]
    assert offences == []
