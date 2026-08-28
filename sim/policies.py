"""The simulator's side of scheduling.

The decisions live in `core.policies` so the app runs the same scheduler on
real orders. What stays here is the waiting: a queued item in the simulator
carries a SimPy event that fires when its station is done with it.
"""

from __future__ import annotations

from dataclasses import dataclass

import simpy

from core.policies import (
    POLICIES,
    BatchPolicy,
    BoundedReorderPolicy,
    FIFOPolicy,
    Policy,
    Queued,
    make_policy,
    plan_batches,
)
from core.types import Item, Order, Task

__all__ = [
    "Pending",
    "Policy",
    "Queued",
    "FIFOPolicy",
    "BatchPolicy",
    "BoundedReorderPolicy",
    "POLICIES",
    "make_policy",
    "plan_batches",
]


@dataclass(slots=True)
class Pending:
    """One item waiting its turn at one station, in virtual time."""

    task: Task
    order: Order
    item: Item
    station: str
    submitted_at: float
    done: simpy.Event
