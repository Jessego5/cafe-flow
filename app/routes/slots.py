"""Pickup windows and their bottleneck-second budgets.

Built at M2 so the capacity accounting is exercised end to end, but inert until
`params.slots.enabled` turns on at M10 — and only if the M7 findings support it.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import day_seconds, get_params, service_date
from app.db import ensure_slots, session_scope
from core.params import SECONDS_PER_MINUTE

router = APIRouter(tags=["slots"])


@router.get("/slots")
async def get_slots() -> dict:
    params = get_params()
    on = service_date(params)
    now_s = day_seconds(params)
    lead_s = params.slots.min_lead_time_min * SECONDS_PER_MINUTE

    with session_scope() as session:
        rows = ensure_slots(session, params, on)
        slots = [
            {
                "slot_id": row.slot_id,
                "starts_at_s": row.starts_at_s,
                "ends_at_s": row.ends_at_s,
                "capacity_s": row.capacity_s,
                "preorder_capacity_s": row.preorder_capacity_s,
                "used_s": row.used_s,
                "remaining_s": max(0.0, row.preorder_capacity_s - row.used_s),
                "bookable": bool(
                    row.is_open
                    and row.starts_at_s - now_s >= lead_s
                    and row.used_s < row.preorder_capacity_s
                ),
            }
            for row in rows
        ]

    return {
        "enabled": params.slots.enabled,
        "width_min": params.slots.width_min,
        "walkup_reserve_fraction": params.slots.walkup_reserve_fraction,
        "min_lead_time_min": params.slots.min_lead_time_min,
        "bottleneck_station": params.bottleneck_station,
        "service_date": on.isoformat(),
        "slots": slots,
    }
