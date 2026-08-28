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

__all__ = [
    "State",
    "LEGAL",
    "TERMINAL",
    "IllegalTransition",
    "place",
    "promise",
    "transition",
]


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



def place(
    order: "Order",
    at: float,
    actor: str = "customer",
    *,
    log: "EventLog | None" = None,
    **payload: Any,
) -> Event:
    """The order's first event: it came into existence.

    Creation has no from-state, so it is not a transition, but it still has to
    be an event — `placed` is the left-hand side of the conservation identity
    and nothing may be counted from mutable state (ground rule 4).
    """
    current = State(order.state)
    if current is not State.PLACED:
        raise IllegalTransition(order.order_id, current, State.PLACED)

    event = Event(
        seq=PENDING_SEQ,
        event_id="",
        t_s=at,
        type=EventType.STATE_CHANGE,
        order_id=order.order_id,
        customer_id=order.customer_id,
        from_state=None,
        to_state=State.PLACED,
        actor=actor,
        channel=order.channel,
        is_simulated=order.is_simulated,
        payload=payload,
    )
    if log is not None:
        event = log.append(event)
    order.history.append(event)
    return event


def promise(
    order: "Order",
    at: float,
    promised_at_s: float,
    actor: str = "system",
    *,
    log: "EventLog | None" = None,
    **payload: Any,
) -> Event:
    """Quote a time the order will be ready by.

    Not a state change — the order is where it was — but it has to be an event,
    because how far a promise missed can only be measured against what was
    actually promised at the time, and that cannot be reconstructed later from
    a config file that has since changed.
    """
    order.promised_at_s = promised_at_s
    event = Event(
        seq=PENDING_SEQ,
        event_id="",
        t_s=at,
        type=EventType.PROMISE_SET,
        order_id=order.order_id,
        customer_id=order.customer_id,
        actor=actor,
        channel=order.channel,
        is_simulated=order.is_simulated,
        payload={"promised_at_s": promised_at_s, **payload},
    )
    if log is not None:
        event = log.append(event)
    order.history.append(event)
    return event


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
