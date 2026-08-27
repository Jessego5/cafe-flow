"""The two staff-facing reads: the bar queue and the pickup display."""

from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import select

from app.config import day_seconds, get_params, service_date, settings
from app.db import OrderItemRow, OrderRow, open_orders, session_scope
from app.routes.common import order_payload
from core.states import State

router = APIRouter(tags=["barista"])


@router.get("/queue")
async def get_queue() -> dict:
    """Full queue state. The client calls this on every (re)connect, so a
    dropped stream can never leave the bar looking at a stale queue."""
    params = get_params()
    now_s = day_seconds(params)
    include_simulated = settings.env.allows_simulated_orders

    with session_scope() as session:
        rows = open_orders(
            session, include_simulated=include_simulated, on=service_date(params)
        )
        orders = [order_payload(row, items, now_s=now_s) for row, items in rows]

    return {
        "now_s": now_s,
        "env": str(settings.env),
        "showing_simulated": include_simulated,
        "orders": orders,
    }


@router.get("/display")
async def get_display() -> dict:
    """Order numbers waiting on the handoff shelf."""
    params = get_params()
    now_s = day_seconds(params)
    on = service_date(params)

    with session_scope() as session:
        statement = (
            select(OrderRow)
            .where(OrderRow.state == str(State.READY), OrderRow.service_date == on.isoformat())
            .order_by(OrderRow.placed_at_s)
        )
        if not settings.env.allows_simulated_orders:
            statement = statement.where(OrderRow.is_simulated == False)  # noqa: E712
        rows = session.exec(statement).all()
        items = {
            row.order_id: session.exec(
                select(OrderItemRow).where(OrderItemRow.order_id == row.order_id)
            ).all()
            for row in rows
        }

    return {
        "now_s": now_s,
        "ready": [
            {
                "number": row.number,
                "order_id": row.order_id,
                "is_simulated": row.is_simulated,
                "ready_for_s": max(0.0, now_s - row.placed_at_s),
                "items": [item.drink for item in items[row.order_id]],
            }
            for row in rows
        ],
    }
