"""Response shapes shared by the routes. Presentation only — no rules."""

from __future__ import annotations

from typing import Sequence

from app.config import day_seconds, get_params
from app.db import OrderItemRow, OrderRow
from core.events import Event

__all__ = ["order_payload", "event_payload", "PAYMENT_STUB"]

#: Payment is out of scope for the research question and is not built (non-goals).
PAYMENT_STUB = {"status": "stubbed", "amount_due_cents": 0}


def order_payload(
    row: OrderRow,
    items: Sequence[OrderItemRow] = (),
    *,
    now_s: float | None = None,
) -> dict:
    params = get_params()
    now_s = day_seconds(params) if now_s is None else now_s
    return {
        "order_id": row.order_id,
        "number": row.number,
        "state": row.state,
        "channel": row.channel,
        # The bar calls this out; `/display` builds its own dict and does not
        # include it, which is the point.
        "customer_name": row.customer_name,
        "is_simulated": row.is_simulated,
        "service_date": row.service_date,
        "placed_at": row.placed_at.isoformat(),
        "placed_at_s": row.placed_at_s,
        "waiting_s": max(0.0, now_s - row.placed_at_s),
        "promised_at_s": row.promised_at_s,
        "price_cents": row.price_cents,
        "bottleneck_cost_s": row.bottleneck_cost_s,
        "slot_id": row.slot_id,
        "items": [
            {
                "item_id": item.item_id,
                "drink": item.drink,
                "milk_type": item.milk_type,
                "variant": item.variant,
                "price_cents": item.price_cents,
            }
            for item in sorted(items, key=lambda i: i.position)
        ],
        "payment": PAYMENT_STUB,
    }


def event_payload(event: Event) -> dict:
    return {
        "seq": event.seq,
        "event_id": event.event_id,
        "t_s": event.t_s,
        "type": str(event.type),
        "from_state": event.from_state,
        "to_state": event.to_state,
        "actor": event.actor,
        "wall_ts": event.wall_ts.isoformat() if event.wall_ts else None,
        "payload": event.payload,
    }
