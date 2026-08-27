# Deploying cafe-flow

One always-on process with a persistent disk. Not serverless: SSE needs
long-lived connections and SQLite needs a real filesystem.

## First deploy

    fly launch --no-deploy -c deploy/fly.toml
    fly volumes create cafe_data --size 1 --region ord
    fly scale count 1                       # the ceiling; fly.toml sets the floor
    fly deploy -c deploy/fly.toml

Then check the three views: `/` student, `/bar` barista, `/pickup` display.

## Backups

Litestream replicates `/data/cafe.db` continuously. Set the credentials as
secrets — without `LITESTREAM_BUCKET` the container runs unreplicated and says
so at boot, which is fine for `dev` and `demo` and not fine for `pilot`.

    fly secrets set \
      LITESTREAM_BUCKET=cafe-flow-backups \
      LITESTREAM_ENDPOINT=https://<account>.r2.cloudflarestorage.com \
      LITESTREAM_ACCESS_KEY_ID=... \
      LITESTREAM_SECRET_ACCESS_KEY=...

**Verify the restore before the pilot, not after.** A backup nobody has
restored is not a backup:

    fly volumes create cafe_restore_test --size 1
    # attach it to a one-off machine, then inside it:
    litestream restore -config /etc/litestream.yml /data/restore-check.db
    sqlite3 /data/restore-check.db "select count(*) from order_events"

## Environments

Three apps, three volumes, three databases. Never one app with a flag.

| Env | `CAFE_ENV` | Database | Simulated orders |
|---|---|---|---|
| dev | `dev` | local file | allowed |
| demo | `demo` | Fly volume | allowed, seeded by the M9 replay client |
| pilot | `pilot` | Fly volume | **rejected at the API with 403** |

In `pilot`, `POST /orders` with `X-Simulated-Order` returns 403 and the barista
view filters simulated orders server-side. A simulated order appearing on the
bar during a real rush destroys trust in the tool permanently.

## Parameters

`params/` lives on the volume, not in the image. The image seeds
`/data/params/base.yaml` on first boot only. Calibration at M8 is a file drop
and a restart:

    fly ssh sftp shell -c deploy/fly.toml
    put params/observed.yaml /data/params/observed.yaml
    fly apps restart cafe-flow-demo

The entrypoint picks up `observed.yaml` automatically when it exists and logs
that it did.

## SSE, and what will bite

* **Proxy buffering.** The stream sends `X-Accel-Buffering: no`. A buffering
  proxy makes SSE appear to work locally and silently fail in production.
* **Idle timeouts.** The heartbeat is 15s (`CAFE_SSE_HEARTBEAT_S`). Keep it
  below whatever idle timeout sits in front of the app.
* **Reconnects.** Clients resume with `Last-Event-ID` *and* refetch `/queue`.
  Resuming the stream alone leaves a client that missed events showing a queue
  that is quietly wrong. Cafe wifi drops; this is not hypothetical.

## Rollback

    fly releases -c deploy/fly.toml
    fly deploy --image <previous-image> -c deploy/fly.toml

Under a minute, which is the M11 requirement. Practise it before the pilot day.
