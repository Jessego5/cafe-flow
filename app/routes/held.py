"""Pre-orders that have been paid for and not yet put in the queue.

The customer scrolls to a time, pays, and stops thinking about it. Nothing
reaches the bar until the *live* queue says ordering now lands by the time they
asked for.

That last part is the whole feature, and the reason this is a server-side hold
rather than a notification. A reminder has to commit to the forecast that sold
the customer their time: once somebody has been told to order, they cannot be
re-timed when the queue moves. Holding the order means the decision happens with
the actual line in hand. Simulated over twelve days at half adoption, a fixed
lead had the median drink ready 59 minutes early; deciding late has it within a
minute, 91% on time.

The release rule is `sim/engine.py::hold_until_release`, character for
character, because the app telling somebody one thing while the model assumes
another is the failure this project keeps guarding against:

    release when  now + estimate + margin >= wanted
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import day_seconds, get_params, now_utc, service_date, settings
from app.db import (
    DbEventLog,
    HeldOrderRow,
    next_order_number,
    open_orders,
    persist_order,
    session_scope,
)
from app.routes.common import order_payload
from app.stream import broadcaster
from core.capacity import StationCapacityModel
from core.menu import Line, make_order
from core.params import ConfigError, Params, format_hhmm, parse_hhmm
from core.promise import load_forecast, plan_for
from core.states import place, promise
from core.types import Channel
from core.waiting import estimate_wait_s, nominal_seconds_per_order, observable_queue_depth

__all__ = ["router", "release_due", "release_loop"]

log = logging.getLogger("cafe.held")

router = APIRouter()


class LineIn(BaseModel):
    drink: str
    milk_type: str | None = None
    variant: str | None = None


class HeldIn(BaseModel):
    lines: list[LineIn]
    wanted_at: str
    customer_id: str | None = None


def _release_clocks(params: Params) -> tuple[float, float]:
    """Poll interval and slack, or a 503 saying holding is not configured.

    Durations live in config (ground rule 1), and a cafe that has not said how
    much slack to leave has not decided whether it wants this feature. Better to
    refuse the hold than to invent the number the whole behaviour turns on.
    """
    poll = params.customers.release_poll_s
    margin = params.customers.release_margin_s
    if poll is None or margin is None:
        raise HTTPException(
            503,
            "holding is not configured: set customers.release_poll_s and "
            "customers.release_margin_s",
        )
    return poll, margin


def _live_wait_s(session: Session, params: Params) -> float | None:
    """What somebody joining right now would wait, from the queue as it is.

    The same estimate `/menu` shows and `sim/engine.py` releases against. None
    outside the staffing plan, where there is nobody on the bar to quote for.
    """
    depth = observable_queue_depth(row.state for row, _ in open_orders(session))
    try:
        per_order = nominal_seconds_per_order(params, params.baristas_at(day_seconds(params)))
    except ConfigError:
        return None
    return estimate_wait_s(depth, per_order)


@router.post("/held", status_code=201)
async def hold_order(body: HeldIn) -> dict:
    """Take payment and hold the order. The server re-derives the timing.

    No `order_at` is accepted from the caller, for the same reason `POST /orders`
    does not accept a quoted ready time: this is a time the cafe will act on, and
    a client should not be able to name it.
    """
    params = get_params()
    _release_clocks(params)
    now_s = day_seconds(params)

    if not body.lines:
        raise HTTPException(400, "a held order needs at least one line")

    try:
        wanted_at_s = parse_hhmm(body.wanted_at, field="wanted_at")
        lines = [Line(line.drink, line.milk_type, line.variant) for line in body.lines]
        forecast = load_forecast(settings.forecast_path)
        quote = plan_for(forecast, params, lines, wanted_at_s, now_s)
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from None

    if wanted_at_s <= now_s:
        raise HTTPException(400, f"{body.wanted_at} is in the past")
    if wanted_at_s > params.meta.end_s:
        raise HTTPException(400, f"the cafe closes at {format_hhmm(params.meta.end_s)}")
    if not quote.achievable:
        raise HTTPException(
            409,
            f"the earliest we can do is {format_hhmm(quote.ready_at_s)}",
        )

    # Priced now so the customer pays what they were shown, not what the menu
    # says whenever the release happens to fire.
    priced = make_order(
        "quote", params, lines=lines, channel=Channel.PREORDER,
        placed_at_s=now_s, customer_id=body.customer_id, is_simulated=False,
    )

    row = HeldOrderRow(
        held_id=uuid.uuid4().hex[:12],
        service_date=service_date(params).isoformat(),
        wanted_at_s=wanted_at_s,
        quoted_order_at_s=quote.order_at_s,
        quoted_ready_at_s=quote.ready_at_s,
        lines_json=json.dumps([line.model_dump() for line in body.lines]),
        customer_id=body.customer_id,
        price_cents=priced.price_cents,
        created_at=now_utc(),
    )
    with session_scope() as session:
        session.add(row)
        session.commit()
        session.refresh(row)

    return {
        "held_id": row.held_id,
        "wanted_at": format_hhmm(row.wanted_at_s),
        "wanted_at_s": row.wanted_at_s,
        # What we expect right now. It moves -- that is the feature -- so the
        # screen is told to say "usually around" rather than count down to it.
        "expected_order_at": format_hhmm(row.quoted_order_at_s),
        "expected_order_at_s": row.quoted_order_at_s,
        "ready_at": format_hhmm(row.quoted_ready_at_s),
        "price_cents": row.price_cents,
        "state": row.state,
    }


@router.get("/held/{held_id}")
async def get_held(held_id: str) -> dict:
    with session_scope() as session:
        row = session.get(HeldOrderRow, held_id)
        if row is None:
            raise HTTPException(404, f"no held order {held_id!r}")
        return {
            "held_id": row.held_id,
            "state": row.state,
            "wanted_at": format_hhmm(row.wanted_at_s),
            "expected_order_at": format_hhmm(row.quoted_order_at_s),
            "released_at": None if row.released_at_s is None else format_hhmm(row.released_at_s),
            "order_id": row.order_id,
            "price_cents": row.price_cents,
        }


@router.delete("/held/{held_id}", status_code=204)
async def cancel_held(held_id: str) -> None:
    """Idempotent: cancelling an already-released or unknown hold is a 204.

    A released hold is a live order and cancelling it is the order's own
    business, not this endpoint's -- but saying so with a 409 would make the
    client handle a race it cannot win.
    """
    with session_scope() as session:
        row = session.get(HeldOrderRow, held_id)
        if row is not None and row.state == "held":
            row.state = "cancelled"
            session.add(row)
            session.commit()


def _release(session: Session, row: HeldOrderRow, params: Params, now_s: float):
    """Put one held order into the queue. Returns its order id and its events."""
    on = service_date(params)
    lines = [Line(**line) for line in json.loads(row.lines_json)]
    order_id = uuid.uuid4().hex[:12]
    number = next_order_number(session, on)

    order = make_order(
        order_id, params, lines=lines, channel=Channel.PREORDER,
        placed_at_s=now_s, customer_id=row.customer_id, is_simulated=False,
    )
    cost_s = StationCapacityModel(params).order_cost(order)
    event_log = DbEventLog(
        session, scenario=str(settings.env), is_simulated=False, policy=params.policy.name,
    )
    place(
        order, at=now_s, actor="app", log=event_log,
        price_cents=order.price_cents, margin_cents=order.margin_cents,
        bottleneck_cost_s=cost_s, items=[item.drink for item in order.items],
        held_id=row.held_id,
    )
    # The promise is the time they asked for, not the time the forecast guessed
    # when they paid. That is what `promise_error` should be judged against.
    promise(
        order, at=now_s, promised_at_s=row.wanted_at_s, log=event_log,
        source="held", held_id=row.held_id,
        quoted_order_at_s=round(row.quoted_order_at_s, 1),
    )
    persist_order(session, order, params, number=number, on=on, bottleneck_cost_s=cost_s)

    row.state = "released"
    row.released_at_s = now_s
    row.order_id = order_id
    session.add(row)
    return order_id, list(event_log.published)


def release_due() -> list[str]:
    """Release every hold the live queue says it is time for.

    Also releases anything that has reached the time it was wanted: a hold that
    never fires is worse than a late drink, and a line growing faster than the
    poll can follow has to be given up on rather than waited out.
    """
    params = get_params()
    poll_margin = (params.customers.release_poll_s, params.customers.release_margin_s)
    if poll_margin[1] is None:
        return []
    margin = poll_margin[1]
    now_s = day_seconds(params)
    on = service_date(params).isoformat()
    released: list[str] = []

    with session_scope() as session:
        rows = session.exec(
            select(HeldOrderRow).where(HeldOrderRow.state == "held")
        ).all()

        # Yesterday's lunch is not worth firing.
        stale = [row for row in rows if row.service_date != on]
        for row in stale:
            row.state = "cancelled"
            session.add(row)

        today = [row for row in rows if row.service_date == on]
        published = []
        if today:
            estimate = _live_wait_s(session, params)
            for row in today:
                due = now_s >= row.wanted_at_s or (
                    estimate is not None and now_s + estimate + margin >= row.wanted_at_s
                )
                if due:
                    order_id, events = _release(session, row, params, now_s)
                    released.append(order_id)
                    published.extend(events)
        session.commit()

    # After the commit: the bar must never be shown an order the database has
    # not accepted.
    if published:
        broadcaster.publish(published)
    if stale:
        log.info("swept %d held order(s) from earlier service dates", len(stale))
    return released


async def release_loop() -> None:
    """The background task. One machine, so no leader election.

    `deploy/fly.toml` sets `min_machines_running = 1` and
    `auto_stop_machines = false`, so there is exactly one of these. Say so here,
    because the day that changes this task quietly double-releases.
    """
    params = get_params()
    poll = params.customers.release_poll_s
    if poll is None or params.customers.release_margin_s is None:
        log.info("held orders: release loop off, no release_poll_s/release_margin_s")
        return
    log.info("held orders: releasing every %.0fs", poll)
    while True:
        try:
            released = await asyncio.to_thread(release_due)
            if released:
                log.info("released %d held order(s): %s", len(released), ", ".join(released))
        except Exception:                       # a bad row must not stop the loop
            log.exception("held order release failed")
        await asyncio.sleep(poll)
