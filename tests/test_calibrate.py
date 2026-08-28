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
    capture, produced = fit_capture_rate(seen, [BASE], overlay, seeds=[0, 1])

    assert 0 < capture <= 1
    assert produced == pytest.approx(seen.orders_per_hour, rel=0.15)

    calibrated = load_params(BASE, overlay={**overlay, "arrivals": {"capture_rate": capture}})
    report = validate(seen, calibrated, seeds=[0, 1])
    assert report.passes
    assert abs(report.volume_error) <= 0.1


def test_a_volume_the_class_blocks_cannot_supply_is_refused(params):
    """Fitting has limits, and reaching them means the arrivals model is wrong
    rather than the knob being mis-set."""
    impossible = read_summary("Ground Truth, 2026-08-27\nwatched: 5 min\norders: 400\n")
    with pytest.raises(ConfigError, match="class_blocks"):
        fit_capture_rate(impossible, [BASE], {}, seeds=[0])


def test_the_balk_gap_is_reported_because_nothing_was_fitted_to_it(seen, params):
    """Volume agreeing proves little — it is what the knob was turned to match.
    Balking is the honest test."""
    overlay = to_overlay(seen, params)
    capture, _ = fit_capture_rate(seen, [BASE], overlay, seeds=[0, 1])
    calibrated = load_params(BASE, overlay={**overlay, "arrivals": {"capture_rate": capture}})

    report = validate(seen, calibrated, seeds=[0, 1])
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
