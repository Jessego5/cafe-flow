"""Quoting a time, in both directions.

The forward one is a lookup. The backward one is the interesting half: the
right moment to order depends on the queue at that moment, which depends on
when you order.
"""

from __future__ import annotations

import pytest

from core.params import ConfigError, load_params
from core.promise import (Forecast, basket_seconds, load_forecast, plan_for,
                          ready_if_ordered_now, typical_basket_seconds)
from core.types import Line

CONFIG = ["params/base.yaml", "params/observed.yaml", "params/fitted_arrivals.yaml"]
HOUR = 3600.0


@pytest.fixture(scope="module")
def params():
    return load_params(*CONFIG)


@pytest.fixture
def calm():
    """Two minutes all day, so arithmetic is checkable by hand."""
    return Forecast(bin_minutes=15.0, opens_at_s=8 * HOUR, quantile=80,
                    mean_wait_s=tuple([120.0] * 32), safe_wait_s=tuple([120.0] * 32))


@pytest.fixture
def spiky():
    """Calm, except a rush from 12:00 to 12:30 that costs twelve minutes."""
    waits = [120.0] * 32
    for index in (16, 17):                     # 12:00 and 12:15
        waits[index] = 720.0
    return Forecast(bin_minutes=15.0, opens_at_s=8 * HOUR, quantile=80,
                    mean_wait_s=tuple(waits), safe_wait_s=tuple(waits))


# --------------------------------------------------------------------------
# forward
# --------------------------------------------------------------------------


def test_order_now_is_a_lookup(params, calm):
    quote = ready_if_ordered_now(calm, params, [Line("drip_coffee")], 9 * HOUR)
    assert quote.order_at_s == 9 * HOUR
    assert quote.ready_at_s == pytest.approx(9 * HOUR + quote.wait_s)
    assert quote.achievable


def test_a_heavier_basket_is_charged_the_difference(params, calm):
    light = ready_if_ordered_now(calm, params, [Line("drip_coffee")], 9 * HOUR)
    heavy = ready_if_ordered_now(
        calm, params, [Line("italian_herb_chicken")] * 3, 9 * HOUR
    )
    assert heavy.wait_s > light.wait_s
    # the forecast is quoted for an average order, so the excess is the basket
    assert heavy.wait_s - light.wait_s == pytest.approx(
        heavy.basket_s - light.basket_s, abs=1.0
    )


# --------------------------------------------------------------------------
# backward, which is the useful direction
# --------------------------------------------------------------------------


def test_wanting_it_later_means_ordering_later(params, calm):
    early = plan_for(calm, params, [Line("drip_coffee")], 10 * HOUR, 9 * HOUR)
    late = plan_for(calm, params, [Line("drip_coffee")], 11 * HOUR, 9 * HOUR)
    assert late.order_at_s > early.order_at_s
    assert early.ready_at_s <= 10 * HOUR
    assert late.ready_at_s <= 11 * HOUR


def test_it_takes_the_latest_start_that_still_lands_in_time(params, calm):
    """Ordering sooner than necessary only means the drink sits going cold."""
    quote = plan_for(calm, params, [Line("drip_coffee")], 10 * HOUR, 8 * HOUR)
    assert quote.slack_s >= 0
    # within one forecast bin: the search cannot be finer than its own resolution
    assert quote.slack_s < calm.bin_minutes * 60


def test_it_orders_around_a_rush_rather_than_into_it(params, spiky):
    """The whole point: collecting at half twelve is cheaper if you order after
    the spike than if you order during it."""
    through = plan_for(spiky, params, [Line("drip_coffee")], 12 * HOUR + 20 * 60, 11 * HOUR)
    after = plan_for(spiky, params, [Line("drip_coffee")], 13 * HOUR, 11 * HOUR)
    assert through.wait_s > after.wait_s
    assert after.order_at_s > 12 * HOUR + 30 * 60


def test_a_time_that_cannot_be_made_says_so(params, spiky):
    """Better than quoting a time that was never available."""
    quote = plan_for(spiky, params, [Line("drip_coffee")], 12 * HOUR + 60, 12 * HOUR)
    assert quote.achievable is False
    assert quote.slack_s < 0
    assert quote.order_at_s == 12 * HOUR         # falls back to ordering now


def test_the_quote_carries_its_reasoning(params, calm):
    quote = ready_if_ordered_now(calm, params, [Line("latte", "oat", "hot")], 9 * HOUR)
    assert quote.basket_s > 0
    assert quote.typical_basket_s == pytest.approx(typical_basket_seconds(params))
    assert basket_seconds(params, [Line("drip_coffee")]) < quote.basket_s


# --------------------------------------------------------------------------
# the file it reads
# --------------------------------------------------------------------------


def test_the_generated_forecast_loads():
    forecast = load_forecast("params/forecast.yaml")
    assert forecast.seeds > 0
    assert len(forecast.mean_wait_s) == len(forecast.safe_wait_s)
    # promises are quoted off a high quantile, never the mean
    assert forecast.quantile >= 75
    assert all(s >= m for s, m in zip(forecast.safe_wait_s, forecast.mean_wait_s))


def test_a_missing_forecast_says_how_to_make_one(tmp_path):
    with pytest.raises(ConfigError, match="sim.forecast"):
        load_forecast(tmp_path / "nothing.yaml")


def test_a_forecast_that_is_not_one_is_refused(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("forecast: {bin_minutes: 15}\n")
    with pytest.raises(ConfigError, match="not a forecast"):
        load_forecast(bad)


def test_a_forecast_must_say_which_quantile_it_quotes(tmp_path):
    """A promise built on an unknown quantile is a guess wearing a uniform."""
    bad = tmp_path / "no_quantile.yaml"
    bad.write_text(
        "forecast:\n  bin_minutes: 15\n  opens_at_s: 27000\n"
        "  mean_wait_s: [60]\n  safe_wait_s: [90]\n"
    )
    with pytest.raises(ConfigError, match="not a forecast"):
        load_forecast(bad)
