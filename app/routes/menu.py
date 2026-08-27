"""The menu, served from params by way of the projection table."""

from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import select

from app.config import get_params
from app.db import MenuItemRow, session_scope

router = APIRouter(tags=["menu"])


@router.get("/menu")
async def get_menu() -> dict:
    params = get_params()
    with session_scope() as session:
        rows = {row.name: row for row in session.exec(select(MenuItemRow)).all()}

    items = []
    for name, spec in params.menu.items():
        row = rows.get(name)
        task_specs, _, price_cents = spec.plan()
        items.append(
            {
                "name": name,
                "price_cents": price_cents,
                "requires_milk": spec.requires_milk,
                "variants": list(spec.variant_names),
                "default_variant": spec.default_variant,
                "service_s": row.service_s if row else None,
                "stations": [task.station for task in task_specs],
                "bottleneck_cost_s": row.bottleneck_cost_s if row else None,
            }
        )

    return {
        "items": items,
        "milks": sorted(params.mix.milk),
        "serve_styles": list(params.mix.serve),
        "bottleneck_station": params.bottleneck_station,
        # every surface states how much of what it shows is still guessed
        "provenance": params.provenance_report().caption(),
    }
