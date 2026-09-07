#!/bin/sh
# Restore, migrate, then start. In that order, and loudly.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
CAFE_DB="${CAFE_DB:-$DATA_DIR/cafe.db}"
export CAFE_DB
export CAFE_WEB_DIST="${CAFE_WEB_DIST:-/app/web/dist}"

mkdir -p "$DATA_DIR/params"

# Params live on the volume, not in the image: recalibrating is a file drop
# plus a restart, never a rebuild. The image seeds the first boot only.
#
# Every calibrated layer is seeded, not just base. Seeding base alone served a
# fresh machine the assumed hours, the assumed staffing and the invented class
# timetable, while the observed files sat in the image next to it saying
# otherwise -- and nothing in the running app said which it was on except the
# provenance line at boot.
for layer in base.yaml observed.yaml hybrid_arrivals.yaml forecast.yaml; do
  if [ -f "/app/params/$layer" ] && [ ! -f "$DATA_DIR/params/$layer" ]; then
    echo "seeding $DATA_DIR/params/$layer from the image"
    cp "/app/params/$layer" "$DATA_DIR/params/$layer"
  fi
done

# An explicitly set CAFE_PARAMS wins. This used to be overwritten
# unconditionally, so there was no way to run a machine on anything but the
# stack below -- not even to reproduce a scenario.
if [ -z "${CAFE_PARAMS:-}" ]; then
  CAFE_PARAMS="$DATA_DIR/params/base.yaml"
  for layer in observed.yaml hybrid_arrivals.yaml; do
    if [ -f "$DATA_DIR/params/$layer" ]; then
      echo "overlaying $layer"
      CAFE_PARAMS="$CAFE_PARAMS:$DATA_DIR/params/$layer"
    fi
  done
fi
export CAFE_PARAMS

# The forecast is read like params and follows them onto the volume, so a
# re-forecast is the same file drop rather than a rebuild.
if [ -f "$DATA_DIR/params/forecast.yaml" ]; then
  export CAFE_FORECAST="${CAFE_FORECAST:-$DATA_DIR/params/forecast.yaml}"
fi

# A fresh machine or a lost volume starts with no database. For a pilot, losing
# the order log means losing the research data, so restore before anything
# touches the file.
if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  if [ ! -f "$CAFE_DB" ]; then
    echo "no database at $CAFE_DB; attempting restore from the replica"
    litestream restore -if-replica-exists -config /etc/litestream.yml "$CAFE_DB"
  else
    echo "database present at $CAFE_DB; not restoring"
  fi
else
  echo "LITESTREAM_BUCKET is unset: running without replication (dev or demo only)"
fi

# Fail at boot, not on the first order of a rush.
python -m app.migrate

# A command given to the container is run instead of the server, after the
# restore and the migration above -- so `docker run cafe-flow python -m
# app.create_staff jess` makes an account rather than silently starting a second
# web server and sitting there. `fly ssh console -C` and `docker exec` bypass
# the entrypoint and were always fine; this is the obvious way in, and it used
# to do the wrong thing quietly.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

SERVE="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-server-header"

if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  exec litestream replicate -config /etc/litestream.yml -exec "$SERVE"
else
  exec $SERVE
fi
