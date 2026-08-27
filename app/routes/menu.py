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
        items.append(
            {
                "name": name,
                "price_cents": spec.price_cents,
                "requires_milk": spec.requires_milk,
                "assembly_s": spec.assembly_s,
                "service_s": row.service_s if row else None,
                "stations": [task.station for task in spec.tasks],
                "bottleneck_cost_s": row.bottleneck_cost_s if row else None,
            }
        )

    return {
        "items": items,
        "milks": sorted(params.mix.milk),
        "bottleneck_station": params.bottleneck_station,
        # every surface states how much of what it shows is still guessed
        "provenance": params.provenance_report().caption(),
    }
