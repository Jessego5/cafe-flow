"""Writes to the menu: what the cafe has run out of.

Availability is an operational fact, not a modelling one. The bagels go at
eleven and come back tomorrow, and neither event should involve a redeploy, so
it lives on the projection row rather than in params, and `seed_menu` leaves it
alone while rewriting everything else.

The simulator knows nothing about it, deliberately. A replay is reproducing a
day that already happened; refusing one of its orders because the counter is out
of bagels today would make history depend on the present.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import MenuItemRow, session_scope
from app.routes.auth import require_staff

__all__ = ["router", "unavailable"]

router = APIRouter()


class AvailabilityIn(BaseModel):
    available: bool


def unavailable(session: Session, names: list[str]) -> list[str]:
    """Which of these the cafe has run out of, in the order given."""
    if not names:
        return []
    rows = {
        row.name: row
        for row in session.exec(
            select(MenuItemRow).where(MenuItemRow.name.in_(set(names)))
        ).all()
    }
    return [name for name in names if name in rows and not rows[name].available]


@router.patch("/menu/{name}")
async def set_availability(
    name: str, body: AvailabilityIn, staff: str = Depends(require_staff)
) -> dict:
    """Mark an item sold out, or back on.

    A PATCH rather than a PUT: everything else about a menu item is projected
    from params and is not the caller's to replace.
    """
    with session_scope() as session:
        row = session.get(MenuItemRow, name)
        if row is None:
            raise HTTPException(404, f"no menu item {name!r}")
        row.available = body.available
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"name": row.name, "available": row.available}
