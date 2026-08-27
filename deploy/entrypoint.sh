#!/bin/sh
# Restore, migrate, then start. In that order, and loudly.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
CAFE_DB="${CAFE_DB:-$DATA_DIR/cafe.db}"
export CAFE_DB
export CAFE_WEB_DIST="${CAFE_WEB_DIST:-/app/web/dist}"

mkdir -p "$DATA_DIR/params"

# Params live on the volume, not in the image: calibration at M8 is a file drop
# plus a restart, never a rebuild. The image only seeds the first boot.
if [ ! -f "$DATA_DIR/params/base.yaml" ]; then
  echo "seeding $DATA_DIR/params/base.yaml from the image"
  cp /app/params/base.yaml "$DATA_DIR/params/base.yaml"
fi

CAFE_PARAMS="$DATA_DIR/params/base.yaml"
if [ -f "$DATA_DIR/params/observed.yaml" ]; then
  echo "overlaying observed parameters"
  CAFE_PARAMS="$CAFE_PARAMS:$DATA_DIR/params/observed.yaml"
fi
export CAFE_PARAMS

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

SERVE="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-server-header"

if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  exec litestream replicate -config /etc/litestream.yml -exec "$SERVE"
else
  exec $SERVE
fi
