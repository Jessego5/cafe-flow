"""Placing orders and moving them through the state machine.

Every rule invoked here lives in `core/`: `core.menu` decides what an order
costs and what work it implies, `core.capacity` prices it in bottleneck-seconds,
and `core.states` decides which moves are legal. This module only translates
HTTP into those calls and persists the result.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, select

from app.config import day_seconds, get_params, service_date, settings
from app.db import (
    DbEventLog,
    events_for,
    load_order,
    next_order_number,
    persist_order,
    release_slot_capacity,
    reserve_slot_capacity,
    session_scope,
    OrderItemRow,
    OrderRow,
    SlotRow,
)
from app.routes.catalog import unavailable
from app.routes.common import event_payload, order_payload
from app.stream import broadcaster
from core.capacity import StationCapacityModel
from core.events import EventType
from core.menu import Line, make_order
from core.params import ConfigError, SECONDS_PER_MINUTE, format_hhmm, parse_hhmm
from core.promise import load_forecast, plan_for, ready_if_ordered_now
from core.states import IllegalTransition, State, amend, place, promise, transition
from core.types import Channel

router = APIRouter(tags=["orders"])

NUMBER_RETRIES = 5  # daily order numbers are unique per day; retry a lost race


class LineIn(BaseModel):
    drink: str
    milk_type: str | None = None
    variant: str | None = None      # the board's hot or iced


class OrderIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1)
    channel: Channel = Channel.WALKUP
    slot_id: str | None = None
    customer_id: str | None = None
    #: The caller showed this customer a ready time before they committed, so
    #: record what was quoted. Off by default, and deliberately so: an order
    #: placed without a quote must move through exactly the states `core` moves
    #: it through, or the app and the simulator no longer describe the same
    #: cafe (ground rule 2). The time itself is still the server's own.
    quoted: bool = False


class TransitionIn(BaseModel):
    to: State
    actor: str = "barista"


class PlanIn(BaseModel):
    """Either direction. Give a `wanted_at` and it works backwards; leave it out
    and it answers for ordering right now."""

    lines: list[LineIn] = Field(min_length=1)
    wanted_at: str | None = None      # HH:MM, local


def _simulated(header: str | None) -> bool:
    """`pilot` refuses simulated orders outright (M2.5 environment table)."""
    wanted = str(header).lower() in {"1", "true", "yes"} if header else False
    if wanted and not settings.env.allows_simulated_orders:
        raise HTTPException(
            403, f"simulated orders are rejected in {settings.env}: this is a live queue"
        )
    return wanted


def _forecast_quote(params, lines, now_s):
    """What the app would tell this customer their order is ready by.

    Quoted here rather than taken from the client: a promise the cafe is going
    to be measured against has to be the cafe's own number, not one a caller
    can name. Returns None when there is no forecast to quote from — an order
    is still an order, it just carries no promise.
    """
    try:
        forecast = load_forecast(settings.forecast_path)
        quote = ready_if_ordered_now(forecast, params, lines, now_s)
    except ConfigError:
        return None
    return forecast, quote


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
            # A replay is reproducing a day that already happened, so it is not
            # refused for something the counter is out of today: that would make
            # history depend on the present.
            if not is_simulated:
                out = unavailable(session, [line.drink for line in body.lines])
                if out:
                    raise HTTPException(409, f"sold out: {', '.join(sorted(set(out)))}")
            number = next_order_number(session, on)
            order_id = uuid.uuid4().hex[:12]

            lines = [Line(line.drink, line.milk_type, line.variant) for line in body.lines]
            try:
                order = make_order(
                    order_id,
                    params,
                    lines=lines,
                    channel=body.channel,
                    placed_at_s=now_s,
                    customer_id=body.customer_id,
                    is_simulated=is_simulated,
                )
            except ConfigError as exc:
                raise HTTPException(400, str(exc)) from None

            cost_s = StationCapacityModel(params).order_cost(order)
            promised_at_s: float | None = None

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
                promised_at_s = slot.ends_at_s

            log = DbEventLog(
                session, scenario=str(settings.env), is_simulated=is_simulated,
                policy=params.policy.name,
            )
            place(
                order,
                at=now_s,
                actor="customer",
                log=log,
                price_cents=order.price_cents,
                margin_cents=order.margin_cents,
                bottleneck_cost_s=cost_s,
                items=[item.drink for item in order.items],
            )
            if promised_at_s is not None:
                promise(order, at=now_s, promised_at_s=promised_at_s, log=log,
                        slot_id=order.slot_id, source="slot")
            elif body.quoted:
                # No window booked, but a time was put in front of someone, and
                # a quote nobody records is a promise nobody can check. Writing
                # it here is what lets `analysis.promise_error` run over this
                # app's own log and say whether the number held.
                quoted = _forecast_quote(params, lines, now_s)
                if quoted is not None:
                    forecast, quote = quoted
                    promised_at_s = quote.ready_at_s
                    promise(
                        order,
                        at=now_s,
                        promised_at_s=promised_at_s,
                        log=log,
                        source="forecast",
                        wait_s=round(quote.wait_s, 1),
                        basket_s=round(quote.basket_s, 1),
                        quantile=forecast.quantile,
                        days=forecast.seeds,
                    )

            # after the promise, so the row records the time that was quoted
            row = persist_order(
                session, order, params, number=number, on=on, bottleneck_cost_s=cost_s
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
                "variant": item.variant,
                "price_cents": item.price_cents,
            }
            for item in order.items
        ]
        return payload

    raise HTTPException(503, "could not allocate an order number")


@router.post("/plan")
async def plan(body: PlanIn) -> dict:
    """When it will be ready, or when to order for a time you have in mind.

    The wait comes from a forecast the simulator produced, so the number quoted
    here is a prediction the model actually made — which means `promise_error`
    can be run over this app's own log afterwards to see whether it held.
    """
    params = get_params()
    now_s = day_seconds(params)

    try:
        lines = [Line(line.drink, line.milk_type, line.variant) for line in body.lines]
        forecast = load_forecast(settings.forecast_path)
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from None

    try:
        if body.wanted_at is None:
            quote = ready_if_ordered_now(forecast, params, lines, now_s)
        else:
            quote = plan_for(
                forecast, params, lines, parse_hhmm(body.wanted_at, field="wanted_at"), now_s
            )
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from None

    return {
        "order_at": format_hhmm(quote.order_at_s),
        "order_at_s": quote.order_at_s,
        "ready_at": format_hhmm(quote.ready_at_s),
        "ready_at_s": quote.ready_at_s,
        "wait_s": round(quote.wait_s, 1),
        "order_in_s": round(max(0.0, quote.order_at_s - now_s), 1),
        "achievable": quote.achievable,
        "wanted_at": None if quote.wanted_at_s is None else format_hhmm(quote.wanted_at_s),
        # what the quote is built on, so it can be argued with
        "basket_s": round(quote.basket_s, 1),
        "typical_basket_s": round(quote.typical_basket_s, 1),
        "from_forecast": {
            "scenario": forecast.scenario,
            "days": forecast.seeds,
            "quantile": forecast.quantile,
        },
    }


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
            "variant": item.variant,
            "price_cents": item.price_cents,
        }
        for item in order.items
    ]
    payload["events"] = [event_payload(event) for event in events]
    return payload


class AmendIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1)


@router.patch("/orders/{order_id}")
async def amend_order(order_id: str, body: AmendIn) -> dict:
    """Change the basket, while nobody has started making it.

    Only from `placed`. Once a barista has accepted the order they are holding
    the cup, and editing what is in it from a phone is not a thing a cafe can
    honour -- so this is a 409 rather than a silent no-op, because the customer
    needs to know their change did not take.

    The amendment is written to the log, not just to the row. Every margin in
    `analysis/` is summed from `placed` events, so an edit that only updated the
    projection would leave the metrics reporting the basket the customer changed
    their mind about. The event carries deltas rather than new totals: the log
    already said what was offered, and this says what changed.
    """
    params = get_params()
    now_s = day_seconds(params)

    with session_scope() as session:
        found = load_order(session, order_id, params)
        if found is None:
            raise HTTPException(404, f"no order {order_id!r}")
        row, order = found

        if order.state is not State.PLACED:
            raise HTTPException(
                409,
                f"order {order_id} is {row.state} and can no longer be changed",
            )

        if not row.is_simulated:
            out = unavailable(session, [line.drink for line in body.lines])
            if out:
                raise HTTPException(409, f"sold out: {', '.join(sorted(set(out)))}")

        lines = [Line(line.drink, line.milk_type, line.variant) for line in body.lines]
        try:
            amended = make_order(
                order_id,
                params,
                lines=lines,
                channel=order.channel,
                placed_at_s=row.placed_at_s,
                customer_id=row.customer_id,
                is_simulated=row.is_simulated,
            )
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from None

        cost_s = StationCapacityModel(params).order_cost(amended)
        log = DbEventLog(
            session, scenario=str(settings.env), is_simulated=row.is_simulated,
            policy=params.policy.name,
        )
        try:
            amend(
                order, amended, at=now_s, actor="customer", log=log,
                bottleneck_delta_s=cost_s - row.bottleneck_cost_s,
            )
        except IllegalTransition as exc:
            raise HTTPException(409, str(exc)) from None

        # A booked slot was sized for the old basket. Rather than reason about a
        # partial re-reservation, give the old cost back and take the new one;
        # if the slot cannot hold it, the amendment is refused and nothing moved.
        if row.slot_id:
            release_slot_capacity(session, row.slot_id, row.bottleneck_cost_s)
            if not reserve_slot_capacity(session, row.slot_id, cost_s):
                raise HTTPException(409, f"slot {row.slot_id} cannot hold the new order")

        # Updated in place rather than re-persisted: the order keeps its id and
        # the number on the pickup display, which is the whole point of amending
        # rather than cancelling and re-placing.
        session.exec(delete(OrderItemRow).where(OrderItemRow.order_id == order_id))
        row.price_cents = amended.price_cents
        row.margin_cents = amended.margin_cents
        row.bottleneck_cost_s = cost_s
        row.updated_at = log.published[-1].wall_ts
        session.add(row)
        session.flush()
        for position, item in enumerate(amended.items):
            session.add(
                OrderItemRow(
                    item_id=item.item_id,
                    order_id=order_id,
                    position=position,
                    drink=item.drink,
                    milk_type=item.milk_type,
                    variant=item.variant,
                    price_cents=item.price_cents,
                    cogs_cents=item.cogs_cents,
                    requires_milk=item.requires_milk,
                )
            )
        session.commit()
        row = session.get(OrderRow, order_id)
        items = session.exec(
            select(OrderItemRow).where(OrderItemRow.order_id == order_id)
        ).all()
        payload = order_payload(row, items, now_s=now_s)

    broadcaster.publish(log.published)
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

        log = DbEventLog(
            session, scenario=str(settings.env), is_simulated=row.is_simulated,
            policy=params.policy.name,
        )
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
