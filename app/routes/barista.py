"""The two staff-facing reads: the bar queue and the pickup display."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends
from sqlmodel import select

from app.config import day_seconds, get_params, service_date, settings
from app.db import OrderItemRow, OrderRow, hydrate, open_orders, session_scope
from app.routes.auth import require_staff
from app.routes.common import order_payload
from core.capacity import StationCapacityModel
from core.params import Params
from core.policies import make_policy, plan_batches
from core.states import State
from core.types import Item
from core.params import ConfigError
from core.waiting import estimate_wait_s, nominal_seconds_per_order, observable_queue_depth

router = APIRouter(tags=["barista"])


@dataclass(slots=True)
class QueuedItem:
    """One item on the bar waiting for a station that can run several at once.

    This is the app's carrier for `core.policies`: the simulator passes one
    holding a SimPy event, the bar passes one holding a row. The scheduler that
    sees them is the same code either way, which is the point — a policy
    measured in an experiment is the policy the bar runs, not a reimplementation
    of it.
    """

    item: Item
    submitted_at: float
    order_id: str
    number: int


def suggested_batches(rows, params: Params, now_s: float) -> list[dict]:
    """What the bar could run together right now, and what it would save.

    Advisory only. The barista decides; this says what the scheduler would do
    and what it is worth, in the same bottleneck-seconds the capacity model and
    the simulator use.
    """
    policy = make_policy(params)
    suggestions: list[dict] = []

    for name, station in params.stations.items():
        if not station.groups_work:
            continue

        model = StationCapacityModel(params, name)
        pending: list[QueuedItem] = []
        for row, item_rows in rows:
            if State(row.state) is State.READY:      # already on the shelf
                continue
            for item in hydrate(row, item_rows, params).items:
                if item.tasks_at(name):
                    pending.append(
                        QueuedItem(
                            item=item,
                            submitted_at=row.placed_at_s,
                            order_id=row.order_id,
                            number=row.number,
                        )
                    )
        if not pending:
            continue

        for batch in plan_batches(policy, name, pending, now_s):
            if len(batch) < 2:
                continue
            items = [entry.item for entry in batch]
            saving_s = sum(model.cost(item) for item in items) - model.batch_cost(items)
            if saving_s <= 0:
                continue
            suggestions.append(
                {
                    "station": name,
                    "size": len(batch),
                    "saving_s": round(saving_s, 1),
                    "key": model.batch_value(items[0]),
                    "items": [
                        {
                            "order_id": entry.order_id,
                            "number": entry.number,
                            "drink": entry.item.drink,
                            "milk_type": entry.item.milk_type,
                            "variant": entry.item.variant,
                        }
                        for entry in batch
                    ],
                }
            )

    suggestions.sort(key=lambda batch: -batch["saving_s"])
    return suggestions


@router.get("/queue")
async def get_queue(staff: str = Depends(require_staff)) -> dict:
    """Full queue state. The client calls this on every (re)connect, so a
    dropped stream can never leave the bar looking at a stale queue.

    Staff only: it lists every order in the shop, with the name each was placed
    under. `/display` is the public view and shows numbers.
    """
    params = get_params()
    now_s = day_seconds(params)
    include_simulated = settings.env.allows_simulated_orders

    with session_scope() as session:
        rows = open_orders(
            session, include_simulated=include_simulated, on=service_date(params)
        )
        orders = [order_payload(row, items, now_s=now_s) for row, items in rows]
        batches = suggested_batches(rows, params, now_s)

    # The same estimate the simulator uses to decide who gives up. If the two
    # disagreed, the cafe would be telling people one thing while the model
    # assumed another.
    depth = observable_queue_depth(order["state"] for order in orders)

    # Outside the staffing plan there is nobody on the bar, so there is no wait
    # to quote — and the bar screen still has to render. Same reasoning as
    # `/menu`: a closed cafe reports no estimate rather than raising, because
    # the queue itself is worth showing whether or not anyone is on shift.
    try:
        per_order = nominal_seconds_per_order(params, params.baristas_at(now_s))
    except ConfigError:
        per_order = None

    return {
        "now_s": now_s,
        "env": str(settings.env),
        "showing_simulated": include_simulated,
        "policy": params.policy.name,
        "orders": orders,
        "batches": batches,
        "queue_depth": depth,
        "wait_estimate_s": None if per_order is None else round(estimate_wait_s(depth, per_order), 1),
        "seconds_per_order": None if per_order is None else round(per_order, 1),
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
