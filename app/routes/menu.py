"""The menu, served from params by way of the projection table."""

from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import select

from app.config import day_seconds, get_params
from app.db import MenuItemRow, open_orders, session_scope
from core.params import ConfigError
from core.waiting import estimate_wait_s, nominal_seconds_per_order, observable_queue_depth

router = APIRouter(tags=["menu"])


@router.get("/menu")
async def get_menu() -> dict:
    params = get_params()
    with session_scope() as session:
        rows = {row.name: row for row in session.exec(select(MenuItemRow)).all()}
        waiting = open_orders(session)
    depth = observable_queue_depth(row.state for row, _ in waiting)

    # Outside the staffing plan there is nobody on the bar, so there is no wait
    # to quote and the endpoint says so rather than raising: the menu is still
    # worth serving when the cafe is shut, and a quoted zero would read as
    # "come now" to someone standing at a locked door.
    try:
        per_order = nominal_seconds_per_order(params, params.baristas_at(day_seconds(params)))
    except ConfigError:
        per_order = None

    items = []
    for name, spec in params.menu.items():
        row = rows.get(name)
        task_specs, _, price_cents = spec.plan()
        items.append(
            {
                "name": name,
                "price_cents": price_cents,
                # posted on the board for the medium size, and only for drinks
                "calories": spec.calories,
                "requires_milk": spec.requires_milk,
                "variants": list(spec.variant_names),
                "default_variant": spec.default_variant,
                "service_s": row.service_s if row else None,
                "stations": [task.station for task in task_specs],
                "bottleneck_cost_s": row.bottleneck_cost_s if row else None,
                # Sold out, in the operational sense. The screen greys it out;
                # the order endpoints refuse it. Absent a projection row the
                # item has never been seeded, which is not the same as sold out.
                "available": row.available if row else True,
            }
        )

    return {
        "items": items,
        "milks": sorted(params.mix.milk),
        "serve_styles": list(params.mix.serve),
        "bottleneck_station": params.bottleneck_station,
        # What the queue looks like before you commit to joining it. The point
        # of showing it is not accuracy for its own sake: someone who sees
        # twelve minutes at noon and comes back at twenty past has moved
        # themselves out of the peak, which is worth more than anything the bar
        # can do about it.
        "queue_depth": depth,
        "wait_estimate_s": None if per_order is None else round(estimate_wait_s(depth, per_order), 1),
        # What one order is worth on its own. An empty queue is not a zero
        # wait — there is still a drink to make — and the menu header says so
        # rather than promising nothing. `/queue` already reports this.
        "seconds_per_order": None if per_order is None else round(per_order, 1),
        # every surface states how much of what it shows is still guessed
        "provenance": params.provenance_report().caption(),
    }
