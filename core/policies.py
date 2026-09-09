"""
This decides what to make next and what to make together. The scheduler lives
in core/ for the same reason prices and legal state moves do: if the app and
the simulator could ever disagree about which two sandwiches go in the press
together, the logic is in the wrong place. That is also what makes a finding
executable rather than merely reported, because an arm that measures a policy
in the simulator and a bar running that policy on real orders are the same code
selected by the same config key, not two implementations somebody has to keep
in step by hand. Whether two items *may* run together at all is a fact about
the station and lives in core.capacity; this module only chooses among the
options that are already legal. Imported by app/ and sim/.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from core.capacity import StationCapacityModel
from core.params import ConfigError, Params
from core.types import Item

__all__ = [
    "Queued",
    "Policy",
    "FIFOPolicy",
    "BatchPolicy",
    "BoundedReorderPolicy",
    "POLICIES",
    "make_policy",
    "plan_batches",
]


@runtime_checkable
class Queued(Protocol):
    """
    Anything waiting its turn at a station.

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

    def reset(self) -> None:
        """
        Forget anything carried between runs. Policies that remember what
        they last made need this so laying out a plan twice gives the same
        plan twice."""


class FIFOPolicy:
    """One at a time, in the order it arrived. The baseline."""

    name = "fifo"

    def __init__(self, params: Params) -> None:
        self.params = params

    def reset(self) -> None:
        return None

    def next_batch(
        self, station: str, pending: Sequence[Queued], now: float
    ) -> list[Queued]:
        return [pending[0]]


class BatchPolicy:
    """
    Group compatible work at the head of the queue.

    Named `batch_milk` in the plan, after the case it was written for: same-milk
    lattes steamed in one pitcher rather than one at a time. The rule is
    general, because the press has the same shape (two sandwiches in one cycle
    rather than two cycles) and on the observed menu that is where the saving
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

    def reset(self) -> None:
        return None

    def _model(self, station: str) -> StationCapacityModel:
        if station not in self._models:
            self._models[station] = StationCapacityModel(self.params, station)
        return self._models[station]

    def _grow(self, station: str, head: Queued, pending: Sequence[Queued]) -> list[Queued]:
        """Everything compatible with `head` that is already waiting."""
        model = self._model(station)
        batch = [head]
        for candidate in pending:
            if candidate is head:
                continue
            if candidate.submitted_at - head.submitted_at > self.lookahead_s:
                continue
            if model.compatible([entry.item for entry in batch], candidate.item):
                batch.append(candidate)
        return batch

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


class BoundedReorderPolicy(BatchPolicy):
    """
    Batch, and prefer work that avoids a changeover, though only within a
    bounded window, and never at the cost of leaving someone stranded.

    Switching the wand from oat to whole means purging and wiping; running the
    oat drinks together avoids that. Left alone, a policy that always chases
    the cheapest changeover starves whoever ordered the unpopular thing, so two
    limits apply. It will only look `reorder_window_s` into the queue, and
    anything that has waited past `starvation_guard_s` goes next regardless of
    what it costs.

    That guard is the whole reason this is safe to run on a real bar: nobody's
    order can be passed over indefinitely because it was inconvenient.
    """

    name = "bounded_reorder"

    def __init__(
        self,
        params: Params,
        lookahead_s: float | None = None,
        window_s: float | None = None,
        guard_s: float | None = None,
    ) -> None:
        super().__init__(params, lookahead_s)
        self.window_s = params.policy.reorder_window_s if window_s is None else window_s
        self.guard_s = params.policy.starvation_guard_s if guard_s is None else guard_s
        self._last_key: dict[str, str | None] = {}

    def reset(self) -> None:
        self._last_key = {}

    def next_batch(
        self, station: str, pending: Sequence[Queued], now: float
    ) -> list[Queued]:
        model = self._model(station)
        head = self._choose_head(station, pending, now, model)
        batch = self._grow(station, head, pending)
        self._last_key[station] = model.batch_value(batch[0].item)
        return batch

    def _choose_head(self, station, pending, now, model) -> Queued:
        # Anyone past the guard goes next, whatever it costs to switch to them.
        for entry in pending:
            if now - entry.submitted_at >= self.guard_s:
                return entry

        wanted = self._last_key.get(station)
        if wanted is None:
            return pending[0]

        # Otherwise look a bounded way down the queue for work that needs no
        # changeover. Anything past the window is not a candidate at all.
        earliest = pending[0].submitted_at
        for entry in pending:
            if entry.submitted_at - earliest > self.window_s:
                break
            if model.batch_value(entry.item) == wanted:
                return entry
        return pending[0]


# Config name -> policy. A name with no implementation must fail at load
# rather than quietly fall back to FIFO and report someone else's numbers.
POLICIES: dict[str, type] = {
    FIFOPolicy.name: FIFOPolicy,
    BatchPolicy.name: BatchPolicy,
    BoundedReorderPolicy.name: BoundedReorderPolicy,
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
    """
    Everything currently queued, in the order and groups it would be run.

    The simulator only ever needs the next batch, because the queue changes
    while it works. A bar display wants the whole plan, so the barista can see
    what is coming as well as what to do now.
    """
    getattr(policy, "reset", lambda: None)()
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
