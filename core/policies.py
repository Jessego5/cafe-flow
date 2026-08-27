"""What to make next, and what to make together.

The scheduler lives in `core/` for the same reason prices and legal state moves
do: if the app and the simulator could ever disagree about which two sandwiches
go in the press together, the logic is in the wrong place (ground rule 2).

That is also what makes a finding executable. An arm that measures a policy in
the simulator and a bar running that policy on real orders are the same code
selected by the same config key, not two implementations that have to be kept
in step by hand.

Whether two items *may* run together is a fact about the station and lives in
`core.capacity`. This module only chooses among the legal options.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from core.capacity import StationCapacityModel
from core.params import ConfigError, Params
from core.types import Item

__all__ = ["Queued", "Policy", "FIFOPolicy", "BatchPolicy", "POLICIES", "make_policy"]


@runtime_checkable
class Queued(Protocol):
    """Anything waiting its turn at a station.

    The simulator's carrier holds a SimPy event; the app's holds a database
    row. Neither belongs here, so the policy sees only the two things it needs
    to decide: what the item is and when it joined the queue.
    """

    item: Item
    submitted_at: float


class Policy(Protocol):
    name: str

    def next_batch(
        self, station: str, pending: Sequence[Queued], now: float
    ) -> list[Queued]:
        """Pick the work to run next. Never empty when `pending` is not."""


class FIFOPolicy:
    """One at a time, in the order it arrived. The baseline."""

    name = "fifo"

    def __init__(self, params: Params) -> None:
        self.params = params

    def next_batch(
        self, station: str, pending: Sequence[Queued], now: float
    ) -> list[Queued]:
        return [pending[0]]


class BatchPolicy:
    """Group compatible work at the head of the queue.

    Named `batch_milk` in the plan, after the case it was written for: same-milk
    lattes steamed in one pitcher rather than one at a time. The rule is
    general, because the press has the same shape — two sandwiches in one cycle
    rather than two cycles — and on the observed menu that is where the saving
    turns out to be.

    Only work already waiting is grouped. Holding a station idle in the hope
    that another sandwich turns up is a different policy, and a riskier one: it
    trades a certain delay for a possible saving.
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
        self, station: str, pending: Sequence[Queued], now: float
    ) -> list[Queued]:
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
POLICIES: dict[str, type] = {
    FIFOPolicy.name: FIFOPolicy,
    BatchPolicy.name: BatchPolicy,
}


def make_policy(params: Params) -> Policy:
    name = params.policy.name
    try:
        factory = POLICIES[name]
    except KeyError:
        raise ConfigError(
            f"policy.name: {name!r} is not implemented yet (have {sorted(POLICIES)})"
        ) from None
    return factory(params)


def plan_batches(
    policy: Policy, station: str, pending: Sequence[Queued], now: float
) -> list[list[Queued]]:
    """Everything currently queued, in the order and groups it would be run.

    The simulator only ever needs the next batch, because the queue changes
    while it works. A bar display wants the whole plan, so the barista can see
    what is coming as well as what to do now.
    """
    remaining = list(pending)
    plan: list[list[Queued]] = []
    while remaining:
        batch = policy.next_batch(station, remaining, now)
        if not batch:                     # a policy that returns nothing would loop
            raise ConfigError(f"{policy.name} returned no work for {station!r}")
        plan.append(batch)
        chosen = {id(entry) for entry in batch}
        remaining = [entry for entry in remaining if id(entry) not in chosen]
    return plan
