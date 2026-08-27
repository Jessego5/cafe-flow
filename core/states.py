"""The order state machine. The single authority on what may happen next.

`app/` and `sim/` both call `transition()`; neither is allowed its own idea of a
legal move (ground rule 2). Every call produces exactly one event, which is what
makes the conservation identity at M3 hold:

    placed == picked_up + balked + abandoned + cancelled + in_flight_at_end
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from core.events import Event, EventType

if TYPE_CHECKING:  # avoids a cycle: core.types imports State from here
    from core.events import EventLog
    from core.types import Order

__all__ = ["State", "LEGAL", "TERMINAL", "IllegalTransition", "transition"]


class State(StrEnum):
    PLACED = "placed"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    PICKED_UP = "picked_up"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    BALKED = "balked"

    @classmethod
    def is_terminal(cls, state: "State") -> bool:
        return state in TERMINAL


#: The whole rulebook. An entry that maps to an empty set is an absorbing state.
LEGAL: dict[State, set[State]] = {
    # A balked customer is recorded as an order that never got made, so that
    # balks stay inside the conservation identity instead of vanishing.
    State.PLACED: {State.ACCEPTED, State.CANCELLED, State.ABANDONED, State.BALKED},
    State.ACCEPTED: {State.IN_PROGRESS, State.CANCELLED, State.ABANDONED},
    State.IN_PROGRESS: {State.READY, State.CANCELLED, State.ABANDONED},
    State.READY: {State.PICKED_UP, State.ABANDONED},
    State.PICKED_UP: set(),
    State.CANCELLED: set(),
    State.ABANDONED: set(),
    State.BALKED: set(),
}

TERMINAL: frozenset[State] = frozenset(
    state for state, onward in LEGAL.items() if not onward
)

#: Terminal states that mean the cafe never earned the order.
LOST: frozenset[State] = frozenset({State.BALKED, State.ABANDONED, State.CANCELLED})

PENDING_SEQ = -1  # replaced by EventLog.append


class IllegalTransition(Exception):
    """A move the rulebook does not allow. Never caught to 'recover'."""

    def __init__(self, order_id: str, from_state: State, to_state: State) -> None:
        allowed = sorted(LEGAL.get(from_state, set()))
        super().__init__(
            f"order {order_id}: {from_state} -> {to_state} is not legal"
            + (f" (allowed: {', '.join(allowed)})" if allowed else f" ({from_state} is terminal)")
        )
        self.order_id = order_id
        self.from_state = from_state
        self.to_state = to_state


def transition(
    order: "Order",
    to: State,
    at: float,
    actor: str = "system",
    *,
    log: "EventLog | None" = None,
    **payload: Any,
) -> Event:
    """Move `order` to `to` at time `at`, returning the event to append.

    Raises IllegalTransition and leaves the order untouched if the move is not
    in LEGAL. Pass `log` to have the event appended (and sequenced) for you;
    otherwise the returned event carries a placeholder `seq` until some
    EventLog adopts it.
    """
    to = State(to)
    current = State(order.state)
    if to not in LEGAL.get(current, set()):
        raise IllegalTransition(order.order_id, current, to)

    event = Event(
        seq=PENDING_SEQ,
        event_id="",
        t_s=at,
        type=EventType.STATE_CHANGE,
        order_id=order.order_id,
        customer_id=order.customer_id,
        from_state=current,
        to_state=to,
        actor=actor,
        channel=order.channel,
        is_simulated=order.is_simulated,
        payload=payload,
    )
    if log is not None:
        event = log.append(event)

    order.state = to
    order.history.append(event)
    return event
