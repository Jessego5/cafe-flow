"""
This reads a till's history and turns it into a demand model. A
point-of-sale export says when people bought and what they bought, which is
enough to replace the two largest guesses in any configuration (the shape of
the day, and the mix of the order book) with something measured, whoever
measured it. It is deliberately generic, with column names passed in, so the
same code reads a Maven Analytics teaching set, a Transact export or a Square
CSV, and because analysis/ imports no runtime it only ever reads. There are
things a sales log cannot tell you and no number of rows will change that: it
records when an order was sold and never when it was made, so service times
are not in there; it is a list of people who bought something and is therefore
structurally blind to everyone who looked at the queue and left, which is the
entire revenue case and has to be counted by a person; and it says nothing
about staffing, queue depth or which station did the work. Run it with python
-m analysis.demand "sales.xlsx" --location "Hell's Kitchen".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

__all__ = ["Columns", "DemandModel", "read_transactions", "build_demand_model"]

SECONDS_PER_HOUR = 3600.0
MINUTES_PER_HOUR = 60.0


@dataclass(frozen=True, slots=True)
class Columns:
    """What the export calls things. Defaults suit the Maven Roasters set."""

    order_id: str = "transaction_id"
    date: str = "transaction_date"
    time: str = "transaction_time"
    quantity: str = "transaction_qty"
    item: str = "product_type"
    price: str = "unit_price"
    location: str | None = "store_location"


@dataclass
class DemandModel:
    """A day's shape and an order book, both counted rather than assumed."""

    source: str
    location: str | None
    days: int
    orders: int
    bin_minutes: float
    opens_at_s: float
    closes_at_s: float
    rate_per_hour: list[float] = field(default_factory=list)
    mix: dict[str, float] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    items_per_order: float = 1.0

    @property
    def peak_per_hour(self) -> float:
        return max(self.rate_per_hour, default=0.0)

    @property
    def orders_per_day(self) -> float:
        return self.orders / self.days if self.days else 0.0

    def summary(self) -> str:
        opens = int(self.opens_at_s // SECONDS_PER_HOUR)
        closes = int(self.closes_at_s // SECONDS_PER_HOUR)
        return (
            f"{self.source}"
            + (f", {self.location}" if self.location else "")
            + f": {self.orders:,} orders over {self.days} days, "
            f"{self.orders_per_day:.0f} a day, peak {self.peak_per_hour:.0f}/h, "
            f"open {opens:02d}:00–{closes:02d}:00"
        )


def read_transactions(path: str | Path, sheet: str | int = 0):
    """
    Load a till export. Excel or CSV; pandas is imported here, not at module
    scope, so `analysis` stays importable without it."""
    import pandas as pd

    target = Path(path)
    if target.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(target, sheet_name=sheet)
    return pd.read_csv(target)


def _seconds_of_day(series):
    import pandas as pd

    text = series.astype(str)
    parsed = pd.to_datetime(text, format="%H:%M:%S", errors="coerce")
    if parsed.isna().any():
        parsed = parsed.fillna(pd.to_datetime(text, format="mixed", errors="coerce"))
    return (
        parsed.dt.hour * SECONDS_PER_HOUR
        + parsed.dt.minute * MINUTES_PER_HOUR
        + parsed.dt.second
    )


def build_demand_model(
    frame,
    *,
    columns: Columns | None = None,
    location: str | None = None,
    bin_minutes: float = 30.0,
    source: str = "transactions",
    keep_items: Sequence[str] | None = None,
    drop_items: Sequence[str] | None = None,
) -> DemandModel:
    """
    Turn a till export into an arrival curve and an item mix.

    The curve is a rate per hour in fixed bins, averaged across every day in the
    file, so one freak morning cannot become the model. Opening hours are taken
    from the data rather than assumed: a shop is open when it is selling.

    `drop_items` exists because a till sells things a bar does not make
    (beans, mugs, a t-shirt) and counting a bag of coffee as an order would
    inflate the very number the whole model turns on.
    """
    import numpy as np
    import pandas as pd

    columns = columns or Columns()
    data = frame.copy()

    if location and columns.location:
        data = data[data[columns.location] == location]
        if data.empty:
            raise ValueError(f"no rows for {location!r}")

    if keep_items is not None:
        data = data[data[columns.item].isin(keep_items)]
    if drop_items is not None:
        data = data[~data[columns.item].isin(drop_items)]
    if data.empty:
        raise ValueError("nothing left after filtering")

    data["_at_s"] = _seconds_of_day(data[columns.time])
    data = data.dropna(subset=["_at_s"])
    days = data[columns.date].nunique()
    if not days:
        raise ValueError("the export covers no days")

    width_s = bin_minutes * MINUTES_PER_HOUR
    opens_at_s = float(np.floor(data["_at_s"].min() / width_s) * width_s)
    closes_at_s = float(np.ceil(data["_at_s"].max() / width_s) * width_s)

    orders = data.drop_duplicates(subset=[columns.order_id])
    edges = np.arange(opens_at_s, closes_at_s + width_s, width_s)
    counts, _ = np.histogram(orders["_at_s"], bins=edges)
    per_hour = counts / days * (SECONDS_PER_HOUR / width_s)

    sold = data.groupby(columns.item)[columns.quantity].sum()
    mix = (sold / sold.sum()).sort_values(ascending=False)

    prices = data.groupby(columns.item)[columns.price].median() if columns.price else pd.Series(dtype=float)

    return DemandModel(
        source=source,
        location=location,
        days=int(days),
        orders=int(len(orders)),
        bin_minutes=bin_minutes,
        opens_at_s=opens_at_s,
        closes_at_s=closes_at_s,
        rate_per_hour=[round(float(rate), 3) for rate in per_hour],
        mix={str(name): round(float(share), 6) for name, share in mix.items()},
        prices={str(name): float(value) for name, value in prices.items()},
        items_per_order=round(float(data[columns.quantity].sum() / len(orders)), 3),
    )


def to_params_fragment(model: DemandModel, *, source: str = "synthetic") -> dict:
    """
    The half of a configuration a till export can actually supply.

    Arrivals and the mix, and nothing else. Which station makes which drink is
    a judgement about a bar, not a fact in a sales log, so it is left for a
    person to write down.
    """
    return {
        "meta": {
            "sim_start": _hhmm(model.opens_at_s),
            "sim_end": _hhmm(model.closes_at_s),
            "source": "assumed",
        },
        "arrivals": {
            "model": "profile",
            "profile": {
                "bin_minutes": model.bin_minutes,
                "rate_per_hour": model.rate_per_hour,
            },
            "capture_rate": 1.0,
            "background_per_hour": 0.0,
            "source": source,
            "source_of": {"capture_rate": "fitted"},
        },
        "mix": {"drink": dict(model.mix), "source": source},
    }


def _hhmm(seconds: float) -> str:
    total = int(seconds) // 60
    return f"{min(23, total // 60):02d}:{total % 60:02d}"


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Turn a till export into the demand half of a configuration."
    )
    parser.add_argument("export", help="an .xlsx or .csv of transactions")
    parser.add_argument("--location", default=None)
    parser.add_argument("--bin-minutes", type=float, default=30.0)
    parser.add_argument("--source-name", default="transactions")
    parser.add_argument(
        "--drop", nargs="*", default=[],
        help="item types the bar does not make to order: beans, mugs, a t-shirt",
    )
    parser.add_argument("--out", default=None, help="write the fragment as YAML")
    args = parser.parse_args()

    frame = read_transactions(args.export)
    model = build_demand_model(
        frame,
        location=args.location,
        bin_minutes=args.bin_minutes,
        source=args.source_name,
        drop_items=args.drop or None,
    )
    print(model.summary())
    print(f"items per order: {model.items_per_order}")
    for name, share in model.mix.items():
        print(f"   {name:<26}{share:7.1%}")

    if args.out:
        import yaml

        Path(args.out).write_text(yaml.safe_dump(to_params_fragment(model), sort_keys=False))
        print(f"wrote {args.out}")
    else:
        print(json.dumps(to_params_fragment(model), indent=2)[:400] + " ...")


if __name__ == "__main__":
    main()
