"""
This is the FastAPI application, and one process serves both the API and the
three built frontends, so there is no CORS configuration to get wrong and no
second origin to keep in step. The API keeps the unprefixed names the spec
fixes, /menu and /orders and /queue and /display and /slots and /stream, while
the HTML views live at /, /bar and /pickup. Run it with python -m uvicorn
app.main:app, after cd web && npm run build has put the views in place.
"""

from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import get_params, service_date, settings
from app.db import ensure_slots, get_engine, init_db, seed_menu, session_scope
from app.routes.auth import router as auth_router
from app.routes.catalog import router as catalog_router
from app.routes.held import release_loop, router as held_router
from app.routes import (
    barista_router,
    health_router,
    menu_router,
    orders_router,
    slots_router,
)
from app.stream import router as stream_router

log = logging.getLogger("cafe")

VIEWS = {
    "/": "student.html",
    "/bar": "barista.html",
    "/pickup": "display.html",
    # the student view inside a drawn phone, for looking at it on a desktop.
    # Served from here rather than opened as a file so its API calls resolve.
    "/frame": "frame.html",
}


def _log_to_the_console() -> None:
    """
    Make the app's own logs visible under uvicorn.

    uvicorn configures its own loggers and leaves the root one alone, so
    everything this app says (which configuration it booted on, whether the
    release loop is running, when a held order fires) went nowhere in a
    container while appearing fine in a test. A deployment whose logs cannot
    say what it came up as is one you have to guess about.
    """
    logger = logging.getLogger("cafe")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_to_the_console()
    params = get_params()
    init_db(get_engine())
    with session_scope() as session:
        seed_menu(session, params)
        ensure_slots(session, params, service_date(params))
    log.info(
        "cafe-flow up: env=%s db=%s bottleneck=%s slots=%s %s",
        settings.env,
        settings.db_path,
        params.bottleneck_station,
        "on" if params.slots.enabled else "off",
        params.provenance_report().caption(),
    )
    # Held pre-orders release themselves against the live queue. Off unless the
    # cafe has said how much slack to leave; the loop logs which.
    releaser = asyncio.create_task(release_loop())
    try:
        yield
    finally:
        releaser.cancel()


class HashedAssets(StaticFiles):
    """
    Vite puts a hash of the contents in every asset filename, so a given URL
    can never change what it returns and is safe to keep for a year. Caching
    them hard is also what makes `no-cache` on the HTML cheap: the document
    revalidates on every visit, and the megabyte behind it does not.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app() -> FastAPI:
    params = get_params()
    app = FastAPI(
        title="cafe-flow",
        version="0.2.0",
        summary="Order-ahead for a campus cafe. Payment and auth are stubbed.",
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(menu_router)
    app.include_router(catalog_router)
    app.include_router(orders_router)
    app.include_router(slots_router)
    app.include_router(held_router)
    app.include_router(barista_router)
    app.include_router(stream_router)

    @app.get("/config", tags=["meta"])
    async def config() -> dict:
        """What the views need to render, and how much of it is guessed."""
        report = params.provenance_report()
        return {
            "env": str(settings.env),
            "cafe": {"name": params.cafe.name, "timezone": params.cafe.timezone},
            "opens_at": params.meta.sim_start,
            "closes_at": params.meta.sim_end,
            "slots_enabled": params.slots.enabled,
            "bottleneck_station": params.bottleneck_station,
            "accepts_simulated_orders": settings.env.allows_simulated_orders,
            "provenance": report.caption(),
            "provenance_counts": report.counts,
        }

    dist = settings.web_dist
    if (dist / "assets").is_dir():
        app.mount("/assets", HashedAssets(directory=dist / "assets"), name="assets")

    for path, filename in VIEWS.items():
        def view(_filename: str = filename):
            target = dist / _filename
            if not target.exists():
                return JSONResponse(
                    {
                        "error": f"{_filename} is not built",
                        "hint": "cd web && npm install && npm run build",
                    },
                    status_code=503,
                )
            # Revalidate every time. The file is small and the check is a 304,
            # and the alternative is what happened here: with no Cache-Control
            # a browser caches the HTML on its own guess, keeps asking for the
            # asset names that HTML was built with, and a deploy reaches nobody
            # who had already opened the page.
            return FileResponse(target, headers={"Cache-Control": "no-cache"})

        app.get(path, include_in_schema=False)(view)

    return app


app = create_app()
