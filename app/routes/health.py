"""
This is the liveness endpoint for the platform's health check. Fly restarts a
machine that fails it, so it tests only the things that actually break in
production (a read-only volume, a full disk, a database that never got
migrated), because a health check that fails on anything richer turns a
working cafe off. Mounted by app/main.py.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlmodel import func, select

from app.config import get_params, now_utc, settings
from app.db import HealthRow, OrderEventRow, session_scope

router = APIRouter(tags=["meta"])


@router.get("/healthz")
async def healthz() -> JSONResponse:
    checks: dict[str, object] = {"env": str(settings.env), "db": str(settings.db_path)}
    try:
        with session_scope() as session:
            checks["events"] = session.exec(
                select(func.count()).select_from(OrderEventRow)
            ).one()
            checks["event_log_readable"] = True

            # writability is proved against `health`, never by appending to the
            # event log
            row = session.get(HealthRow, 1) or HealthRow(id=1, checked_at=now_utc())
            row.checked_at = now_utc()
            session.add(row)
            session.commit()
            checks["event_log_writable"] = True

        checks["params"] = get_params().meta.scenario
        checks["provenance"] = get_params().provenance_report().caption()
    except Exception as exc:  # a failed check must say what failed
        checks["ok"] = False
        checks["error"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(checks, status_code=503)

    checks["ok"] = True
    return JSONResponse(checks)
