"""Placing orders and moving them through the state machine.

Every rule invoked here lives in `core/`: `core.menu` decides what an order
costs and what work it implies, `core.capacity` prices it in bottleneck-seconds,
and `core.states` decides which moves are legal. This module only translates
HTTP into those calls and persists the result.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.config import day_seconds, get_params, service_date, settings
from app.db import (
    DbEventLog,
    OrderRow,
    events_for,
    load_order,
    next_order_number,
    persist_order,
    release_slot_capacity,
    reserve_slot_capacity,
    session_scope,
    SlotRow,
)
from app.routes.common import event_payload, order_payload
from app.stream import broadcaster
from core.capacity import StationCapacityModel
from core.menu import make_order
from core.params import ConfigError, SECONDS_PER_MINUTE
from core.states import IllegalTransition, State, place, transition
from core.types import Channel

router = APIRouter(tags=["orders"])

NUMBER_RETRIES = 5  # daily order numbers are unique per day; retry a lost race


class LineIn(BaseModel):
    drink: str
    milk_type: str | None = None


class OrderIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1)
    channel: Channel = Channel.WALKUP
    slot_id: str | None = None
    customer_id: str | None = None


class TransitionIn(BaseModel):
    to: State
    actor: str = "barista"


def _simulated(header: str | None) -> bool:
    """`pilot` refuses simulated orders outright (M2.5 environment table)."""
    wanted = str(header).lower() in {"1", "true", "yes"} if header else False
    if wanted and not settings.env.allows_simulated_orders:
        raise HTTPException(
            403, f"simulated orders are rejected in {settings.env}: this is a live queue"
        )
    return wanted


@router.post("/orders", status_code=201)
async def create_order(
    body: OrderIn,
    x_simulated_order: str | None = Header(default=None),
) -> dict:
    params = get_params()
    is_simulated = _simulated(x_simulated_order)
    on = service_date(params)
    now_s = day_seconds(params)

    if body.slot_id is not None and not params.slots.enabled:
        raise HTTPException(400, "slots are disabled; orders go straight to the queue")
    if body.slot_id is not None and body.channel is Channel.WALKUP:
        raise HTTPException(400, "walk-up orders do not take a slot")
    if params.slots.enabled and body.channel is Channel.PREORDER and body.slot_id is None:
        raise HTTPException(400, "pre-orders need a slot_id while slots are enabled")

    for attempt in range(NUMBER_RETRIES):
        with session_scope() as session:
            number = next_order_number(session, on)
            order_id = uuid.uuid4().hex[:12]

            try:
                order = make_order(
                    order_id,
                    params,
                    lines=[(line.drink, line.milk_type) for line in body.lines],
                    channel=body.channel,
                    placed_at_s=now_s,
                    customer_id=body.customer_id,
                    is_simulated=is_simulated,
                )
            except ConfigError as exc:
                raise HTTPException(400, str(exc)) from None

            cost_s = StationCapacityModel(params).order_cost(order)

            if body.slot_id is not None:
                slot = session.get(SlotRow, body.slot_id)
                if slot is None or slot.service_date != on.isoformat():
                    raise HTTPException(404, f"no slot {body.slot_id!r} today")
                lead_s = params.slots.min_lead_time_min * SECONDS_PER_MINUTE
                if slot.starts_at_s - now_s < lead_s:
                    raise HTTPException(
                        409, f"slot {slot.slot_id} is inside the {params.slots.min_lead_time_min} "
                        "minute lead time"
                    )
                if not reserve_slot_capacity(session, slot.slot_id, cost_s):
                    raise HTTPException(409, f"slot {slot.slot_id} is full")
                order.slot_id = slot.slot_id
                order.promised_at_s = slot.ends_at_s

            row = persist_order(
                session, order, params, number=number, on=on, bottleneck_cost_s=cost_s
            )
            log = DbEventLog(session, scenario=str(settings.env), is_simulated=is_simulated)
            place(
                order,
                at=now_s,
                actor="customer",
                log=log,
                price_cents=order.price_cents,
                bottleneck_cost_s=cost_s,
                items=[item.drink for item in order.items],
            )

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if attempt == NUMBER_RETRIES - 1:
                    raise HTTPException(503, "could not allocate an order number") from None
                continue

            session.refresh(row)
            payload = order_payload(row, [], now_s=now_s)

        broadcaster.publish(log.published)
        payload["items"] = [
            {
                "item_id": item.item_id,
                "drink": item.drink,
                "milk_type": item.milk_type,
                "price_cents": item.price_cents,
            }
            for item in order.items
        ]
        return payload

    raise HTTPException(503, "could not allocate an order number")


@router.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict:
    params = get_params()
    with session_scope() as session:
        found = load_order(session, order_id, params)
        if found is None:
            raise HTTPException(404, f"no order {order_id!r}")
        row, order = found
        events = events_for(session, order_id)
        payload = order_payload(row, [])

    payload["items"] = [
        {
            "item_id": item.item_id,
            "drink": item.drink,
            "milk_type": item.milk_type,
            "price_cents": item.price_cents,
        }
        for item in order.items
    ]
    payload["events"] = [event_payload(event) for event in events]
    return payload


@router.post("/orders/{order_id}/transition")
async def move_order(order_id: str, body: TransitionIn) -> dict:
    """Advance one order. Idempotent by (order, target state).

    A barista on a laggy connection taps twice; the second tap must not skip a
    state or write a second event.
    """
    params = get_params()
    now_s = day_seconds(params)

    with session_scope() as session:
        found = load_order(session, order_id, params)
        if found is None:
            raise HTTPException(404, f"no order {order_id!r}")
        row, order = found

        if order.state is body.to:
            payload = order_payload(row, [], now_s=now_s)
            payload["idempotent"] = True
            return payload

        log = DbEventLog(session, scenario=str(settings.env), is_simulated=row.is_simulated)
        try:
            transition(order, body.to, at=now_s, actor=body.actor, log=log)
        except IllegalTransition as exc:
            raise HTTPException(409, str(exc)) from None

        if body.to is State.CANCELLED and row.slot_id:
            release_slot_capacity(session, row.slot_id, row.bottleneck_cost_s)

        row.state = str(order.state)
        row.updated_at = log.published[-1].wall_ts
        session.add(row)
        session.commit()
        session.refresh(row)
        payload = order_payload(row, [], now_s=now_s)

    broadcaster.publish(log.published)
    payload["idempotent"] = False
    return payload
