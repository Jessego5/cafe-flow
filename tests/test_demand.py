"""Reading a till's history into a demand model.

The tests build their own transactions rather than downloading anyone's, so
they say exactly what the answer should be and run without a network.
"""

from __future__ import annotations

import pytest

from analysis.demand import Columns, build_demand_model, to_params_fragment

pd = pytest.importorskip("pandas")


def frame(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "transaction_id", "transaction_date", "transaction_time",
            "transaction_qty", "store_location", "unit_price", "product_type",
        ],
    )


def steady(orders_per_hour: int, hours: range, days: int, item="latte", price=5.0):
    """A shop selling at a known, flat rate, so the model has a right answer."""
    rows, order_id = [], 0
    for day in range(days):
        for hour in hours:
            for index in range(orders_per_hour):
                minute = int(60 * index / orders_per_hour)
                order_id += 1
                rows.append([
                    order_id, f"2023-01-{day + 1:02d}", f"{hour:02d}:{minute:02d}:00",
                    1, "Somewhere", price, item,
                ])
    return frame(rows)


# --------------------------------------------------------------------------
# the shape of the day
# --------------------------------------------------------------------------


def test_a_flat_rate_reads_back_as_a_flat_rate():
    model = build_demand_model(steady(12, range(8, 11), days=4), bin_minutes=60)
    assert model.days == 4
    assert model.orders == 12 * 3 * 4
    assert model.rate_per_hour == [12.0, 12.0, 12.0]
    assert model.peak_per_hour == 12.0
    assert model.orders_per_day == pytest.approx(36.0)


def test_opening_hours_come_from_the_data():
    """A shop is open when it is selling; nobody has to write it down."""
    model = build_demand_model(steady(6, range(7, 12), days=2), bin_minutes=60)
    assert model.opens_at_s == 7 * 3600
    assert model.closes_at_s == 12 * 3600
    assert len(model.rate_per_hour) == 5


def test_a_busy_hour_shows_up_as_a_busy_hour():
    quiet = steady(4, range(9, 11), days=2)
    rush = steady(40, range(8, 9), days=2)
    rush["transaction_id"] += 10_000
    model = build_demand_model(pd.concat([quiet, rush]), bin_minutes=60)
    assert model.rate_per_hour == [40.0, 4.0, 4.0]


def test_one_freak_morning_does_not_become_the_model():
    """Rates are averaged across every day in the file."""
    normal = steady(10, range(8, 9), days=9)
    freak = steady(100, range(8, 9), days=1)
    freak["transaction_date"] = "2023-02-01"
    freak["transaction_id"] += 10_000

    model = build_demand_model(pd.concat([normal, freak]), bin_minutes=60)
    assert model.days == 10
    assert model.rate_per_hour == [19.0]          # not 100, and not 10


# --------------------------------------------------------------------------
# the order book
# --------------------------------------------------------------------------


def test_the_mix_is_what_was_sold():
    rows = steady(3, range(8, 9), days=1, item="latte")
    beans = steady(1, range(8, 9), days=1, item="beans")
    beans["transaction_id"] += 1000
    model = build_demand_model(pd.concat([rows, beans]), bin_minutes=60)
    assert model.mix == {"latte": pytest.approx(0.75), "beans": pytest.approx(0.25)}


def test_things_the_bar_does_not_make_are_dropped():
    """A till sells mugs and bags of beans. Counting a bag of beans as an order
    inflates the one number the whole model turns on."""
    drinks = steady(4, range(8, 9), days=1, item="latte")
    retail = steady(4, range(8, 9), days=1, item="Housewares")
    retail["transaction_id"] += 1000

    both = pd.concat([drinks, retail])
    assert build_demand_model(both, bin_minutes=60).rate_per_hour == [8.0]

    made = build_demand_model(both, bin_minutes=60, drop_items=["Housewares"])
    assert made.rate_per_hour == [4.0]
    assert set(made.mix) == {"latte"}


def test_filtering_to_one_shop():
    here = steady(5, range(8, 9), days=1)
    there = steady(5, range(8, 9), days=1)
    there["store_location"] = "Elsewhere"
    there["transaction_id"] += 1000

    model = build_demand_model(pd.concat([here, there]), location="Elsewhere", bin_minutes=60)
    assert model.orders == 5
    assert model.location == "Elsewhere"

    with pytest.raises(ValueError, match="no rows"):
        build_demand_model(here, location="Nowhere")


def test_an_export_with_nothing_in_it_says_so():
    with pytest.raises(ValueError, match="nothing left"):
        build_demand_model(steady(2, range(8, 9), days=1), drop_items=["latte"])


def test_column_names_are_the_caller_s_business():
    """The same code should read a Transact export or a Square CSV."""
    renamed = steady(3, range(8, 9), days=1).rename(
        columns={"transaction_id": "order", "product_type": "sku"}
    )
    model = build_demand_model(
        renamed, columns=Columns(order_id="order", item="sku"), bin_minutes=60
    )
    assert model.orders == 3
    assert set(model.mix) == {"latte"}


# --------------------------------------------------------------------------
# and out the other side, as configuration
# --------------------------------------------------------------------------


def test_the_fragment_is_marked_synthetic_not_observed():
    """A generated dataset is realistic in shape and is not a record of
    anything that happened, and the provenance has to say so."""
    model = build_demand_model(steady(10, range(8, 10), days=3), bin_minutes=30)
    fragment = to_params_fragment(model)

    assert fragment["arrivals"]["model"] == "profile"
    assert fragment["arrivals"]["source"] == "synthetic"
    assert fragment["mix"]["source"] == "synthetic"
    assert fragment["arrivals"]["source_of"]["capture_rate"] == "fitted"
    assert fragment["meta"]["sim_start"] == "08:00"
    assert fragment["meta"]["sim_end"] == "10:00"
    assert len(fragment["arrivals"]["profile"]["rate_per_hour"]) == 4
