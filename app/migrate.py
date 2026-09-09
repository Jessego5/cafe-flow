"""
This applies the schema and the startup data before the server accepts traffic.
It is run by deploy/entrypoint.sh rather than at import time, so a container
that cannot migrate fails at boot with a clear message instead of failing on
the first order of a rush. Run it with python -m app.migrate.
"""

from __future__ import annotations

import logging
import sys

from app.config import get_params, service_date, settings
from app.db import ensure_slots, get_engine, init_db, seed_menu, session_scope

log = logging.getLogger("cafe.migrate")


def migrate() -> None:
    params = get_params()
    init_db(get_engine())
    with session_scope() as session:
        items = seed_menu(session, params)
        slots = ensure_slots(session, params, service_date(params))
    log.info(
        "migrated %s: %d menu items, %d slots for %s (%s)",
        settings.db_path,
        items,
        len(slots),
        service_date(params),
        params.provenance_report().caption(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        migrate()
    except Exception as exc:
        print(f"migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
