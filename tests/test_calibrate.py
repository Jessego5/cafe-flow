"""M8: turning a counted rush into parameters, and refusing to lie about it."""

from __future__ import annotations

import pytest
import yaml

from analysis.calibrate import (
    Observation,
    fit_capture_rate,
    read_summary,
    to_overlay,
    validate,
)
from core.params import ConfigError, load_params

BASE = "params/base.yaml"


def runner(params, seed):
    """`analysis/` never imports a runtime, so the caller supplies one."""
    from sim.engine import run

    return run(params, seed).log

RUSH = """Ground Truth, 2026-08-27
watched: 15 min
orders: 23
iced: 14 of 23
with food: 6 of 23
walked out: 3
longest line: 9
press: 214s, 198s, 231s (avg 214s, 2 in at a time)
"""


@pytest.fixture(scope="module")
def params():
    return load_params(BASE)


@pytest.fixture(scope="module")
def seen():
    return read_summary(RUSH)


# --------------------------------------------------------------------------
# reading what the counter produced
# --------------------------------------------------------------------------


def test_a_counted_rush_reads_back(seen):
    assert seen.where == "Ground Truth"
    assert seen.on == "2026-08-27"
    assert (seen.orders, seen.minutes) == (23, 15.0)
    assert seen.orders_per_hour == pytest.approx(92.0)
    assert seen.iced_share == pytest.approx(14 / 23)
    assert seen.food_share == pytest.approx(6 / 23)
    assert seen.balks_per_hour == pytest.approx(12.0)
    assert seen.press_seconds == [214.0, 198.0, 231.0]
    assert seen.press_size == 2


def test_a_half_finished_session_still_reads(params):
    """Someone who only managed the order count should not lose the afternoon
    to a parser."""
    partial = read_summary("Ground Truth, 2026-08-27\nwatched: 10 min\norders: 14\n")
    assert partial.orders == 14
    assert partial.iced_share is None
    assert partial.food_share is None
    assert partial.press_mean_s is None

    overlay = to_overlay(partial, params)
    assert "mix" not in overlay and "stations" not in overlay


def test_anything_unrecognised_is_kept_as_a_note():
    seen = read_summary("orders: 3\nregister person never made a drink\n")
    assert seen.notes == ["register person never made a drink"]


def test_an_observation_with_no_clock_will_not_pretend(params):
    with pytest.raises(ConfigError, match="elapsed time"):
        Observation(orders=10).orders_per_hour


# --------------------------------------------------------------------------
# what it writes down
# --------------------------------------------------------------------------


def test_counts_become_observed_parameters(seen, params):
    overlay = to_overlay(seen, params)

    assert overlay["mix"]["serve"]["iced"] == pytest.approx(14 / 23, abs=1e-3)
    assert overlay["mix"]["source_of"]["serve"] == "observed"
    assert overlay["stations"]["panini_press"]["run_s"] == pytest.approx(214.3, abs=0.1)
    assert overlay["stations"]["panini_press"]["batch_size"] == 2
    assert overlay["stations"]["panini_press"]["source_of"]["run_s"] == "observed"


def test_the_rewritten_mix_hits_the_counted_food_share(seen, params):
    overlay = to_overlay(seen, params)
    merged = load_params(BASE, overlay=overlay)

    food = {
        name for name, spec in merged.menu.items()
        if any(task.station in ("panini_press", "food_counter") for task in spec.tasks)
    }
    share = sum(s for name, s in merged.mix.drink.items() if name in food)
    assert share == pytest.approx(seen.food_share, abs=1e-6)
    assert sum(merged.mix.drink.values()) == pytest.approx(1.0, abs=1e-9)

    # the shape within each group is untouched: nobody counted individual drinks
    before, after = params.mix.drink, merged.mix.drink
    assert before["latte"] / before["drip_coffee"] == pytest.approx(
        after["latte"] / after["drip_coffee"]
    )


def test_the_overlay_is_a_config_file_like_any_other(seen, params, tmp_path):
    overlay = to_overlay(seen, params)
    path = tmp_path / "observed.yaml"
    path.write_text(yaml.safe_dump(overlay, sort_keys=False))

    merged = load_params(BASE, path)
    assert merged.meta.scenario == "ground_truth_observed"
    assert merged.source_of("mix.serve.iced") == "observed"
    assert merged.source_of("stations.panini_press.run_s") == "observed"
    # untouched parameters keep the provenance they had
    assert merged.source_of("stations.group_head.shot_s") == "published"


# --------------------------------------------------------------------------
# the fit, and the gate
# --------------------------------------------------------------------------


def test_the_fit_reproduces_the_volume_that_was_counted(seen, params):
    overlay = to_overlay(seen, params)
    capture, produced = fit_capture_rate(seen, [BASE], overlay, runner, seeds=[0, 1])

    assert 0 < capture <= 1
    assert produced == pytest.approx(seen.orders_per_hour, rel=0.15)

    calibrated = load_params(BASE, overlay={**overlay, "arrivals": {"capture_rate": capture}})
    report = validate(seen, calibrated, runner, seeds=[0, 1])
    assert report.passes
    assert abs(report.volume_error) <= 0.1


def test_a_volume_the_class_blocks_cannot_supply_is_refused(params):
    """Fitting has limits, and reaching them means the arrivals model is wrong
    rather than the knob being mis-set."""
    impossible = read_summary("Ground Truth, 2026-08-27\nwatched: 5 min\norders: 400\n")
    with pytest.raises(ConfigError, match="class_blocks"):
        fit_capture_rate(impossible, [BASE], {}, runner, seeds=[0])


def test_the_balk_gap_is_reported_because_nothing_was_fitted_to_it(seen, params):
    """Volume agreeing proves little — it is what the knob was turned to match.
    Balking is the honest test."""
    overlay = to_overlay(seen, params)
    capture, _ = fit_capture_rate(seen, [BASE], overlay, runner, seeds=[0, 1])
    calibrated = load_params(BASE, overlay={**overlay, "arrivals": {"capture_rate": capture}})

    report = validate(seen, calibrated, runner, seeds=[0, 1])
    assert report.balk_ratio is not None
    assert report.observed_balks_per_hour == pytest.approx(12.0)

    if not 0.5 <= report.balk_ratio <= 2.0:
        assert "balk_tolerance_min" in report.render()


def test_calibration_moves_the_provenance(seen, params):
    overlay = to_overlay(seen, params)
    calibrated = load_params(BASE, overlay=overlay)

    before = params.provenance_report()
    after = calibrated.provenance_report()
    assert after.assumed_fraction < before.assumed_fraction
    assert after.counts["observed"] > before.counts["observed"]


def test_calibration_needs_no_simulator_to_be_tested(seen, params):
    """The arithmetic is separable from the thing that runs a day: a stub
    runner is enough to exercise the fit."""
    from core.events import EventLog, EventType
    from core.states import State

    def flat(params, seed):
        """A cafe where volume rises exactly with the capture rate."""
        log = EventLog("stub", seed)
        count = int(params.arrivals.capture_rate * 600)
        for index in range(count):
            log.emit(EventType.STATE_CHANGE, 36000.0 + index, order_id=f"o{index}",
                     to_state=State.PLACED, channel="walkup")
        return log

    capture, produced = fit_capture_rate(seen, [BASE], {}, flat, seeds=[0])
    assert 0 < capture < 1
    assert produced == pytest.approx(seen.orders_per_hour, rel=0.2)


# --------------------------------------------------------------------------
# the field checklist, which is mostly answers rather than counts
# --------------------------------------------------------------------------

FIELD = """Ground Truth, 2026-08-27
watched: 15 min
food machine: microwave, holds 1
walked out: 3 (line was 8, 9, 6)
line every minute: 2, 3, 5, 7, 9, 8, 6, 4, 3, 2, 2, 3, 5, 4, 3
till person makes drinks: sometimes
baristas at peak: 3
hours: 07:30-15:00
caesar salad: 11.00
matcha/chai iced: no
"""


@pytest.fixture(scope="module")
def field():
    return read_summary(FIELD)


def test_the_checklist_reads_back(field):
    assert field.machine == "microwave"
    assert field.holds == 1
    assert field.cashier == "sometimes"
    assert field.baristas == 3
    assert (field.opens, field.closes) == ("07:30", "15:00")
    assert field.walked_out == 3
    assert field.balk_lines == [8, 9, 6]
    assert field.typical_balk_line == pytest.approx(23 / 3)


def test_what_the_machine_is_decides_how_many_it_holds(params):
    """The whole reason to ask: a press takes two side by side and a microwave
    takes one, and that is what removes the batching."""
    for answer, run_s, holds in [
        ("microwave", 60.0, 1), ("high-speed oven", 45.0, 1), ("panini press", 240.0, 2)
    ]:
        seen = read_summary(f"Ground Truth, 2026-08-27\nfood machine: {answer}\n")
        press = to_overlay(seen, params)["stations"]["panini_press"]
        assert (press["run_s"], press["batch_size"]) == (run_s, holds)


def test_a_stopwatch_beats_the_conventional_figure(params):
    """Somebody timing it outranks the class average for that machine."""
    seen = read_summary(
        "Ground Truth, 2026-08-27\n"
        "food machine: panini press\n"
        "press: 95s, 105s (avg 100s, 2 in at a time)\n"
    )
    press = to_overlay(seen, params)["stations"]["panini_press"]
    assert press["run_s"] == pytest.approx(100.0)
    assert press["source_of"]["run_s"] == "observed"


def test_hours_and_staffing_come_across(field, params):
    overlay = to_overlay(field, params)
    merged = load_params(BASE, overlay=overlay)
    assert merged.meta.sim_start == "07:30"
    assert merged.baristas_at(11 * 3600) == 3
    assert merged.source_of("meta.sim_start") == "observed"


def test_a_dedicated_cashier_is_raised_rather_than_quietly_modelled(params):
    """serve() always takes a barista for the register, so the shared
    assumption lives in the code. Emitting a config change would model
    something the engine cannot do."""
    from analysis.calibrate import open_questions

    seen = read_summary("Ground Truth, 2026-08-27\ntill person makes drinks: never\n")
    assert "stations" not in to_overlay(seen, params)
    questions = open_questions(seen)
    assert any("engine change" in q for q in questions)


def test_balks_without_a_depth_are_flagged(params):
    from analysis.calibrate import open_questions

    seen = read_summary("Ground Truth, 2026-08-27\nwatched: 10 min\nwalked out: 4\n")
    assert seen.balk_lines == []
    assert any("tolerance cannot be fitted" in q for q in open_questions(seen))


def test_the_depth_people_gave_up_at_is_checked_against_the_model(field, params):
    """A second unfitted comparison: the rate says how many left, the depth
    says how patient they were."""
    overlay = to_overlay(field, params)
    calibrated = load_params(BASE, overlay=overlay)
    report = validate(field, calibrated, runner, seeds=[0, 1])

    assert report.observed_balk_line == pytest.approx(23 / 3)
    assert report.modelled_balk_line is not None
    assert "gave up" in report.render()


# --------------------------------------------------------------------------
# the queue over time
# --------------------------------------------------------------------------


def test_the_queue_samples_read_back(field):
    assert field.line_samples == [2, 3, 5, 7, 9, 8, 6, 4, 3, 2, 2, 3, 5, 4, 3]
    assert field.typical_line == pytest.approx(4.4, abs=0.05)
    assert field.busiest_line == 9


def test_the_depth_at_a_balk_is_biased_high(field):
    """It only ever reads the queue when it was long enough that someone left,
    which is why the timed samples are the honest measure."""
    assert field.typical_balk_line > field.typical_line


def test_an_order_is_counted_once_while_it_waits():
    """Counting a transition into each waiting state would add the same order
    three times on its way through, and the depth would only ever climb."""
    from analysis.calibrate import _queue_over_time
    from analysis.metrics import Interval
    from core.events import EventLog, EventType
    from core.states import State

    log = EventLog("hand", 1)
    for state, at in [(State.PLACED, 0), (State.ACCEPTED, 10), (State.IN_PROGRESS, 20),
                      (State.READY, 200), (State.PICKED_UP, 210)]:
        log.emit(EventType.STATE_CHANGE, float(at), order_id="a", to_state=state)

    depths = _queue_over_time(log, Interval(0.0, 300.0), every_s=60.0)
    assert depths == [1.0, 1.0, 1.0, 1.0, 0.0]


def test_the_fit_falls_back_to_the_queue_when_nobody_counted_orders():
    """The checklist collects depth, not volume — which is what the original
    plan fitted against anyway."""
    seen = read_summary(FIELD)
    assert seen.orders == 0
    overlay = to_overlay(seen, load_params(BASE))
    capture, produced = fit_capture_rate(seen, [BASE], overlay, runner, seeds=[0, 1])
    assert 0 < capture < 1
    assert produced == pytest.approx(seen.typical_line, rel=0.35)


def test_an_observation_with_neither_measure_is_refused():
    seen = read_summary("Ground Truth, 2026-08-27\nfood machine: microwave\n")
    with pytest.raises(ConfigError, match="nothing to fit against"):
        fit_capture_rate(seen, [BASE], {}, runner, seeds=[0])


def test_the_gate_judges_whatever_was_actually_collected(field):
    overlay = to_overlay(field, load_params(BASE))
    capture, _ = fit_capture_rate(field, [BASE], overlay, runner, seeds=[0, 1])
    overlay.setdefault("arrivals", {})["capture_rate"] = capture
    report = validate(field, load_params(BASE, overlay=overlay), runner, seeds=[0, 1])

    assert report.fitted_against == "queue"
    assert report.queue_error is not None
    assert "queue" in report.render()
    # Never claims a volume nobody counted. Asserted on the row rather than on
    # "0/h", which this used to look for and which matches inside any modelled
    # rate ending in a zero -- "140/h" broke it the first time one did.
    assert not any(
        line.strip().startswith("volume") for line in report.render().splitlines()
    ), report.render()


# --------------------------------------------------------------------------
# clock-stamped observation
# --------------------------------------------------------------------------

TIMED = """Ground Truth, 2026-08-27
watched: 10:46-11:04, 16 min recording (1 pause)
food machine: microwave, holds 1
walked out: 3 (10:51 at 5, 10:53 at 7, 10:59 at 9)
line every 2 min: 10:46 2, 10:48 3, 10:50 5, 10:52 7, 10:58 9, 11:00 6, 11:02 4
till person makes drinks: sometimes
baristas at peak: 3
"""


def test_a_timed_observation_keeps_its_clock():
    seen = read_summary(TIMED)
    assert (seen.watched_from, seen.watched_to) == ("10:46", "11:04")
    assert seen.minutes == 16.0
    assert seen.sample_times[:3] == ["10:46", "10:48", "10:50"]
    assert seen.line_samples == [2, 3, 5, 7, 9, 6, 4]
    assert seen.balk_times == ["10:51", "10:53", "10:59"]
    assert seen.balk_lines == [5, 7, 9]


def test_the_pause_shows_as_a_gap_not_as_readings():
    """Nothing between 10:52 and 10:58 — because nobody was watching, which is
    the point of being able to pause."""
    seen = read_summary(TIMED)
    minutes = [
        int(at.split(":")[0]) * 60 + int(at.split(":")[1]) for at in seen.sample_times
    ]
    gaps = [b - a for a, b in zip(minutes, minutes[1:])]
    assert max(gaps) == 6
    assert gaps.count(2) == len(gaps) - 1


def test_an_untimed_observation_still_reads():
    """The older format, without clock times, has to keep working."""
    seen = read_summary(
        "Ground Truth, 2026-08-27\nwatched: 15 min\n"
        "walked out: 2 (line was 6, 8)\n"
        "line every minute: 3, 4, 6, 8\n"
    )
    assert seen.minutes == 15.0
    assert seen.line_samples == [3, 4, 6, 8]
    assert seen.balk_lines == [6, 8]
    assert seen.sample_times == []


def test_a_timed_observation_calibrates(params):
    seen = read_summary(TIMED)
    overlay = to_overlay(seen, params)
    capture, produced = fit_capture_rate(seen, [BASE], overlay, runner, seeds=[0, 1, 2, 3])
    assert 0 < capture < 1
    assert produced == pytest.approx(seen.typical_line, rel=0.35)


def test_the_fit_survives_a_stumble_in_the_objective():
    """Each evaluation averages a handful of simulated days, so it carries
    noise. One misstep sends bisection the wrong way with no route back, and
    keeping the last midpoint rather than the best candidate seen would return
    whatever it happened to land on."""
    from core.events import EventLog, EventType
    from core.states import State

    seen = read_summary(TIMED)
    target = seen.typical_line

    def wobbly(params, seed):
        """Queue depth rises with the capture rate, with a dip partway that a
        naive bisection would fall into."""
        capture = params.arrivals.capture_rate
        depth = capture * 100
        if 0.045 < capture < 0.055:
            depth *= 0.2

        log = EventLog("stub", seed)
        for index in range(max(1, int(depth))):        # placed together, never leave
            log.emit(EventType.STATE_CHANGE, 36000.0, order_id=f"o{index}",
                     to_state=State.PLACED, channel="walkup")
        return log

    capture, produced = fit_capture_rate(seen, [BASE], {}, wobbly, seeds=[0])
    assert 0 < capture < 1
    assert abs(produced - target) < 1.5, (capture, produced, target)
