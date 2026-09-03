"""Whether a customer joins the queue at all, and by which channel.

Until now the model assumed infinite patience: nobody ever left, so a long
queue cost nothing and throughput was a ceiling nobody would reach in practice.
This is where a wait acquires a consequence.

A walk-up arrives, looks at the line, estimates what it will cost them, and
leaves if that exceeds what they will put up with. Someone who ordered ahead has
already committed and never balks — which is the whole of the pre-order case,
and why the balk count is the revenue argument.
"""

from __future__ import annotations

from statistics import mean

import numpy as np

from core.params import SECONDS_PER_MINUTE, ConfigError, Params
from core.types import Channel
from core.waiting import estimate_wait_s, nominal_seconds_per_order, observable_queue_depth

__all__ = [
    "sample_minutes",
    "nominal_seconds_per_order",
    "estimate_wait_s",
    "observable_queue_depth",
    "draw_channel",
    "preorder_lead_s",
]


def sample_minutes(dist, rng: np.random.Generator) -> float:
    """Draw from one of the distributions the config allows, in minutes."""
    kind = dist.dist
    if kind == "lognormal":
        return float(rng.lognormal(mean=np.log(dist.median), sigma=dist.sigma))
    if kind == "normal":
        return float(rng.normal(dist.mean, dist.sigma))
    if kind == "constant":
        # Draw and discard. Every distribution has to consume exactly one
        # number, or swapping a lognormal for a constant re-shuffles every
        # later draw in the run and nothing else stays comparable.
        rng.random()
        return float(dist.value)
    raise ConfigError(f"cannot draw from distribution {kind!r}")


def draw_channel(params: Params, rng: np.random.Generator) -> Channel:
    """Walk up, or order ahead. Adoption is a config fraction today and an
    observable at the pickup shelf once someone counts."""
    return (
        Channel.PREORDER
        if rng.random() < params.customers.preorder_adoption
        else Channel.WALKUP
    )


def preorder_lead_s(params: Params) -> float:
    """How far ahead someone orders: one class block.

    Taken from the gap between the blocks themselves, so a timetable with
    90-minute periods moves this without touching any code.
    """
    ends = sorted(block.ends_at_s for block in params.arrivals.class_blocks)
    gaps = [later - earlier for earlier, later in zip(ends, ends[1:])]
    if not gaps:
        # no timetable to take it from, so an hour: long enough to be ordering
        # ahead rather than queueing, short enough that the drink is still worth
        # collecting
        return 60.0 * SECONDS_PER_MINUTE
    return float(mean(gaps))
