"""Scheduling policies: what the next piece of work at a station should be.

A station that can run several items at a time needs someone to decide what to
run together. That decision is a policy; whether two items *may* run together is
a fact about the station and lives in `core.capacity` (ground rule 2).

The plan sketches `next_task(pending, now) -> Task | Batch`. Pending work is
queued per station rather than per order, so the signature here is
`next_batch(station, pending, now) -> list[Pending]`: same idea, but it can say
"these four together" without inventing an order to hang them on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

import simpy

from core.capacity import StationCapacityModel
from core.params import ConfigError, Params
from core.types import Item, Order, Task

__all__ = ["Pending", "Policy", "FIFOPolicy", "BatchPolicy", "make_policy"]


@dataclass(slots=True)
class Pending:
    """One item waiting its turn at one station."""

    task: Task
    order: Order
    item: Item
    station: str
    submitted_at: float
    done: simpy.Event

    @property
    def age_s(self) -> float:
        return self.submitted_at


@runtime_checkable
class Policy(Protocol):
    name: str

    def next_batch(
        self, station: str, pending: Sequence[Pending], now: float
    ) -> list[Pending]:
        """Pick the work to run next. Never empty when `pending` is not."""


class FIFOPolicy:
    """One at a time, in the order it arrived. The baseline."""

    name = "fifo"

    def __init__(self, params: Params) -> None:
        self.params = params

    def next_batch(
        self, station: str, pending: Sequence[Pending], now: float
    ) -> list[Pending]:
        return [pending[0]]


class BatchPolicy:
    """Group compatible work at the head of the queue.

    Named `batch_milk` in the plan, after the case it was written for: four oat
    lattes steamed together pay the pitcher setup once instead of four times.
    The rule is general, because the press has the same shape — three sandwiches
    in one cycle rather than three cycles — and on the observed menu that is
    where the larger saving is.

    Only work already waiting is grouped. Holding the station idle in the hope
    that a fourth latte turns up would be a different policy, and a riskier one.
    """

    name = "batch_milk"

    def __init__(self, params: Params, lookahead_s: float | None = None) -> None:
        self.params = params
        self.lookahead_s = (
            params.policy.lookahead_s if lookahead_s is None else lookahead_s
        )
        self._models: dict[str, StationCapacityModel] = {}

    def _model(self, station: str) -> StationCapacityModel:
        if station not in self._models:
            self._models[station] = StationCapacityModel(self.params, station)
        return self._models[station]

    def next_batch(
        self, station: str, pending: Sequence[Pending], now: float
    ) -> list[Pending]:
        model = self._model(station)
        head = pending[0]
        batch = [head]

        for candidate in pending[1:]:
            if candidate.submitted_at - head.submitted_at > self.lookahead_s:
                continue
            if model.compatible([entry.item for entry in batch], candidate.item):
                batch.append(candidate)
        return batch


#: Config name -> policy. A name with no implementation must fail at load
#: rather than quietly fall back to FIFO and report someone else's numbers.
POLICIES = {
    FIFOPolicy.name: FIFOPolicy,
    BatchPolicy.name: BatchPolicy,
}


def make_policy(params: Params) -> Policy:
    name = params.policy.name
    try:
        factory = POLICIES[name]
    except KeyError:
        raise ConfigError(
            f"policy.name: {name!r} is not implemented yet "
            f"(have {sorted(POLICIES)})"
        ) from None
    return factory(params)
