#!/bin/sh
# Prove the backup mechanism reproduces the database, using a filesystem
# replica so it runs anywhere. This checks litestream, SQLite and the schema;
# the production drill against object storage is in deploy/README.md, and a
# backup nobody has restored is not a backup.
#
#   docker run --rm --entrypoint /usr/local/bin/verify-restore.sh cafe-flow
set -eu

WORK="${WORK:-/tmp/restore-check}"
rm -rf "$WORK"
mkdir -p "$WORK/replica"

export CAFE_DB="$WORK/cafe.db"
export CAFE_PARAMS="${CAFE_PARAMS:-/app/params/base.yaml}"

cat > "$WORK/litestream.yml" <<YAML
dbs:
  - path: $CAFE_DB
    replicas:
      - type: file
        path: $WORK/replica
        sync-interval: 200ms
YAML

echo "1. building a database with a few orders in it"
python -m app.migrate
python - <<'PY'
from app.config import get_params, service_date
from app.db import DbEventLog, persist_order, session_scope
from core.capacity import StationCapacityModel
from core.menu import make_order
from core.states import State, place, transition

params = get_params()
on = service_date(params)
with session_scope() as session:
    for number in range(1, 6):
        order = make_order(f"restore-{number}", params, lines=[("latte", "oat")],
                           placed_at_s=28800.0 + number)
        cost = StationCapacityModel(params).order_cost(order)
        persist_order(session, order, params, number=number, on=on, bottleneck_cost_s=cost)
        log = DbEventLog(session, scenario="restore-check")
        place(order, at=order.placed_at_s, log=log)
        transition(order, State.ACCEPTED, at=order.placed_at_s + 1, log=log)
    session.commit()
print("   wrote 5 orders")
PY

echo "2. replicating"
litestream replicate -config "$WORK/litestream.yml" &
LITESTREAM_PID=$!
sleep 2
kill "$LITESTREAM_PID" 2>/dev/null || true
wait "$LITESTREAM_PID" 2>/dev/null || true

echo "3. restoring into a fresh path"
litestream restore -config "$WORK/litestream.yml" -o "$WORK/restored.db" "$CAFE_DB"

echo "4. comparing"
python - <<'PY'
import os
import sqlite3
import sys

work = os.environ.get("WORK", "/tmp/restore-check")


def snapshot(path):
    connection = sqlite3.connect(path)
    try:
        orders = connection.execute("select count(*) from orders").fetchone()[0]
        events = connection.execute(
            "select seq, event_id, order_id, to_state from order_events order by seq"
        ).fetchall()
        return orders, events
    finally:
        connection.close()


original = snapshot(f"{work}/cafe.db")
restored = snapshot(f"{work}/restored.db")

if original != restored:
    print(f"   MISMATCH: original {original[0]} orders / {len(original[1])} events, "
          f"restored {restored[0]} orders / {len(restored[1])} events")
    sys.exit(1)

print(f"   match: {original[0]} orders, {len(original[1])} events, identical event log")
PY

echo "restore verified"
